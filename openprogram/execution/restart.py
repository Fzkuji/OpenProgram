"""Bounded worker-restart continuation through canonical events and commands."""

from __future__ import annotations

import asyncio
import logging
from time import time

from .model import CommandStatus, ExecutionStatus

_log = logging.getLogger(__name__)
_REQUEST = "execution.restart.requested"
_SETTLED = "execution.restart.settled"


def window_seconds() -> int:
    from openprogram.setup import _read_config

    settings = _read_config().get("execution", {})
    if not isinstance(settings, dict):
        return 0
    value = settings.get("auto_resume_window_seconds", 7200)
    return value if type(value) is int and value >= 0 else 0


def record_intent(
    store, connection, execution, *, interrupted_at, seconds, pause_command_id=None
):
    """Called inside the transaction owning the interruption/pause intent."""
    store._append_event(
        connection,
        execution_id=execution.execution_id,
        execution_version=execution.status_version,
        kind=_REQUEST,
        payload=dict(
            interrupted_at=interrupted_at,
            resume_before=interrupted_at + seconds,
            expected_version=execution.status_version,
            pause_command_id=pause_command_id,
        ),
        created_at=time(),
    )


def crash_checkpoint(service, connection, execution):
    """Only a current, opted-in checkpoint can survive abandoned ownership."""
    if not execution.capabilities.pause or not execution.checkpoint_head_id:
        return None
    checkpoint = service.checkpoints._get(connection, execution.checkpoint_head_id)
    if (
        checkpoint is None
        or checkpoint.execution_id != execution.execution_id
        or checkpoint.revision_id != execution.revision_id
    ):
        return None
    if (
        checkpoint.created_by_attempt_id != execution.current_attempt_id
        and execution.owner_lease.get("resume_checkpoint_id")
        != checkpoint.checkpoint_id
    ):
        return None
    seconds = checkpoint.state_refs.get("restart_window_seconds", 0)
    if type(seconds) is not int or seconds <= 0:
        return None
    # A later committed action makes an older checkpoint unsafe to replay.
    if connection.execute(
        "SELECT 1 FROM effects WHERE execution_id = ? AND status = 'committed' AND updated_at > ? LIMIT 1",
        (execution.execution_id, checkpoint.created_at),
    ).fetchone():
        return None
    attempt = service.attempts._require(connection, execution.current_attempt_id)
    return max(attempt.updated_at, execution.updated_at), seconds


def _service(runner, execution_id):
    if runner._execution_store.get_job_agent_input(execution_id) is not None:
        return runner._execution_control
    from . import default_control_service

    return default_control_service()


def prepare_shutdown(runner) -> None:
    """Request cooperative pause before the worker's bounded process teardown."""
    from openprogram.self_update.maintenance import maintenance_blocks

    runner._restart_shutdown = True
    shutdown_event = getattr(runner, "_shutdown_event", None)
    if shutdown_event is not None:
        shutdown_event.set()
        runner._dispatch_wake.set()
    seconds = window_seconds()
    if seconds == 0 or maintenance_blocks("worker_restart"):
        return
    store = runner._execution_store
    for execution in store.list_nonterminal():
        if (
            execution.status is not ExecutionStatus.RUNNING
            or not execution.capabilities.pause
        ):
            continue
        command_id = f"worker-restart:pause:{execution.current_attempt_id}"
        # A normal safe point can advance the version before pause acceptance.
        # Retrying never transfers shutdown ownership to a replacement attempt.
        for retry in range(3):
            try:
                with store._transaction() as connection:
                    current = store._require_execution(
                        connection, execution.execution_id
                    )
                    if (
                        current.status is not ExecutionStatus.RUNNING
                        or current.current_attempt_id != execution.current_attempt_id
                    ):
                        break
                    if not any(
                        e.kind == _REQUEST
                        and e.payload.get("pause_command_id") == command_id
                        for e in store.list_events(execution.execution_id)
                    ):
                        record_intent(
                            store,
                            connection,
                            current,
                            interrupted_at=time(),
                            seconds=seconds,
                            pause_command_id=command_id,
                        )
                asyncio.run(
                    _service(runner, execution.execution_id).request_pause(
                        command_id=command_id,
                        execution_id=execution.execution_id,
                        expected_version=current.status_version,
                        actor={"surface": "worker-restart"},
                    )
                )
                break
            except Exception as exc:
                if getattr(exc, "code", None) == "stale_version" and retry < 2:
                    continue
                _log.exception(
                    "could not checkpoint execution %s during shutdown",
                    execution.execution_id,
                )
                break


def _settle(store, execution, event, outcome):
    with store._transaction() as connection:
        store._append_event(
            connection,
            execution_id=execution.execution_id,
            execution_version=execution.status_version,
            kind=_SETTLED,
            payload=dict(request_sequence=event.sequence, outcome=outcome),
            created_at=time(),
        )


def reconcile(runner) -> None:
    """Resume only the exact restart-owned pause before its fixed deadline."""
    from openprogram.self_update.maintenance import maintenance_blocks

    if getattr(runner, "_restart_shutdown", False) or maintenance_blocks(
        "worker_restart"
    ):
        return
    store = runner._execution_store
    for execution in store.list_nonterminal():
        if (
            execution.status is not ExecutionStatus.PAUSED
            or execution.current_attempt_id
        ):
            continue
        events = store.list_events(execution.execution_id)
        requests = [e for e in events if e.kind == _REQUEST]
        if not requests:
            continue
        event = requests[-1]
        if any(
            e.kind == _SETTLED and e.payload.get("request_sequence") == event.sequence
            for e in events
        ):
            continue
        data = event.payload
        command_id = (
            f"worker-restart:continue:{execution.execution_id}:{event.sequence}"
        )
        existing = store.get_command(command_id)
        if existing is not None:
            if existing.status in {CommandStatus.APPLIED, CommandStatus.REJECTED}:
                _settle(store, execution, event, existing.status.value)
            continue
        pause_id = data["pause_command_id"]
        pause = store.get_command(pause_id) if pause_id else None
        expected = (
            pause.result_version
            if pause is not None and pause.status is CommandStatus.APPLIED
            else data["expected_version"]
            if pause_id is None
            else None
        )
        if expected != execution.status_version:
            _settle(store, execution, event, "execution_changed")
            continue
        if (
            time() < data["interrupted_at"]
            or time() > data["resume_before"]
            or window_seconds() == 0
        ):
            _settle(store, execution, event, "expired")
            continue
        service = _service(runner, execution.execution_id)
        if service.effects.list_unresolved(execution.execution_id):
            continue
        # A current user turn/wait in this conversation retains its ownership.
        if any(
            e.execution_id != execution.execution_id
            and e.status is not ExecutionStatus.PAUSED
            for e in store.list_nonterminal(session_id=execution.session_id)
        ):
            continue
        try:
            kwargs = dict(
                command_id=command_id,
                execution_id=execution.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "worker-restart"},
            )
            if store.get_job_agent_input(execution.execution_id) is not None:
                runner.queue_job_resume(**kwargs, step=False)
            else:
                asyncio.run(service.request_continue(**kwargs))
        except Exception:
            _log.exception("automatic restart failed for %s", execution.execution_id)
