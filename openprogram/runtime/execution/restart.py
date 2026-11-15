"""Worker-restart continuation through durable canonical events and commands."""

from __future__ import annotations

import asyncio
import json
import logging
from time import time

from .model import RESTART_NOT_CONTINUED, TERMINAL_EXECUTION_STATUSES, CommandStatus, ExecutionStatus
from .process_owner import owned_by_another_process, process_owner_may_be_alive

_log = logging.getLogger(__name__)
_REQUEST = "execution.restart.requested"
_SETTLED = "execution.restart.settled"
_SHUTDOWN_PAUSE = "worker-restart:pause:"
_CHECK_IN_SPEAKER = "runtime/always_on"
_AUTOMATIC_SPEAKERS = frozenset({_CHECK_IN_SPEAKER, "runtime/scheduler"})
_PAUSED_BY_RESTART = frozenset({"restart_recoverable", "restart_pending"})


def _request_sequence(row) -> object:
    """The restart request a settled event answers."""
    payload = json.loads(row["payload_json"])
    return payload.get("request_sequence") if isinstance(payload, dict) else None


def window_seconds() -> int:
    """-1 preserves run intent indefinitely; zero is an explicit opt-out."""
    from openprogram.state.config import read_config

    settings = read_config().get("execution", {})
    if not isinstance(settings, dict):
        return 0
    value = settings.get("auto_resume_window_seconds", -1)
    return value if type(value) is int and value >= -1 else 0


def resume_deadline(interrupted_at: float, seconds: int) -> float | None:
    return None if seconds == -1 else interrupted_at + seconds


def record_intent(
    store, connection, execution, *, interrupted_at, seconds, pause_command_id=None, mode="checkpoint"
):
    """Called inside the transaction owning the interruption/pause intent."""
    store._append_event(
        connection,
        execution_id=execution.execution_id,
        execution_version=execution.status_version,
        kind=_REQUEST,
        payload=dict(
            interrupted_at=interrupted_at,
            resume_before=resume_deadline(interrupted_at, seconds),
            expected_version=execution.status_version,
            pause_command_id=pause_command_id,
            mode=mode,
        ),
        created_at=time(),
    )


def journal_head(service, connection, execution):
    """(state, pointer) of everything the execution's turn has recorded, or None."""
    if not execution.session_id:
        return None
    from .agent_journal import latest_pointer, load_journal

    head = execution.checkpoint_head_id or execution.source_checkpoint_id
    checkpoint = service.checkpoints._get(connection, head) if head else None
    base = checkpoint.state_refs.get("agent_journal") if checkpoint is not None else None
    pointer = latest_pointer(execution.session_id, execution.execution_id, base)
    if pointer is None:
        return None
    return load_journal(execution.session_id, pointer), pointer


def publish_position(service, connection, execution, journal, seconds) -> bool:
    """Name the turn's last recorded step as the place to resume from."""
    from .agent_journal import manifest_fields

    if journal is None or not journal[0].has_decision or execution.current_attempt_id is None:
        return False
    attempt = connection.get("attempts", execution.current_attempt_id)
    if attempt is None or attempt["status"] != "active":
        return False
    kind, frontier, refs = manifest_fields(journal[0], journal[1], restart_window_seconds=seconds)
    if kind not in execution.capabilities.safe_point_kinds:
        return False
    service.checkpoints._publish_in_transaction(
        connection, execution_id=execution.execution_id,
        expected_version=execution.status_version, revision_id=execution.revision_id,
        parent_checkpoint_id=execution.checkpoint_head_id, frontier=frontier, state_refs=refs,
        completed_actions=(), effect_receipts=(), child_frontier={}, pending_command_ids=(),
        created_by_attempt_id=execution.current_attempt_id, owner_lost=True,
    )
    return True


def crash_checkpoint(service, connection, execution, journal):
    """Publish where an ownerless turn stood, when it may resume on its own."""
    seconds = window_seconds()
    if not execution.capabilities.pause or seconds == 0:
        return None
    if not publish_position(service, connection, execution, journal, seconds):
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
    from openprogram.operations.self_update.control.maintenance import maintenance_blocks

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
        # A turn another process owns is not stopping with this one, and what
        # this process holds for it may be long out of date.
        if (
            execution.status is not ExecutionStatus.RUNNING
            or not execution.capabilities.pause
            or owned_by_another_process(execution.owner_lease)
        ):
            continue
        command_id = f"{_SHUTDOWN_PAUSE}{execution.current_attempt_id}"
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


_LIVE = frozenset({
    ExecutionStatus.QUEUED, ExecutionStatus.RUNNING,
    ExecutionStatus.PAUSING, ExecutionStatus.CANCELLING,
})
_STOPPING = frozenset({ExecutionStatus.PAUSING, ExecutionStatus.CANCELLING})


def _superseded(store, execution, interrupted_at) -> bool:
    """Whether the user began another turn of the conversation after ``interrupted_at``.

    Check-ins and scheduled runs sit beside the conversation, so one of those
    starting says nothing about the user having moved on.
    """
    if not execution.session_id:
        return False
    from contextlib import closing
    with closing(store._connect()) as connection:
        later = connection.rows(
            "executions", session_id=execution.session_id,
            where=lambda row: row["execution_id"] != execution.execution_id
            and (row["parent_execution_id"] or "") != execution.execution_id
            and row["created_at"] > interrupted_at,
        )
    return any(not _part_of_automatic_work(store, row["execution_id"]) for row in later)


def _conversation_is_busy(store, execution) -> bool:
    """Another turn of this conversation holds it, so this one waits.

    A run's own ancestors never count: they are what it is working for, and a
    resumed parent must not hold back its own child. Nor does a turn stopping
    with no process left to finish stopping, or one awaiting reconciliation.
    """
    own = {execution.execution_id} | {a.execution_id for a in _ancestors(store, execution)}
    return any(
        other.execution_id not in own
        and other.status in _LIVE
        and not (other.status in _STOPPING and not process_owner_may_be_alive(other.owner_lease))
        for other in store.list_nonterminal(session_id=execution.session_id)
    )


def conversation_settling(store, session_id) -> bool:
    """A restart has not yet decided what becomes of this conversation's turns."""
    from contextlib import closing
    with closing(store._connect()) as connection:
        own = {row["execution_id"] for row in connection.rows("executions", session_id=session_id)}
        answered = {(row["execution_id"], _request_sequence(row))
                    for row in connection.rows("execution_events", kind=_SETTLED)}
        open_intent = any(row["execution_id"] in own and (row["execution_id"], row["sequence"]) not in answered
                          for row in connection.rows("execution_events", kind=_REQUEST))
    return open_intent or any(other.status in _LIVE for other in store.list_nonterminal(session_id=session_id))


def not_continued(store, execution) -> None:
    """Whoever waits on this cut-off turn hears that no restart will continue it."""
    try:
        from .hooks import restart_abandoned
        restart_abandoned(store, execution)
    except Exception:  # noqa: BLE001 - the restart's own bookkeeping goes on
        _log.exception("could not report the end of %s", execution.execution_id)


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


def _is_scheduled_run(store, execution) -> bool:
    """A scheduled task's turn: on its own branch, beside the conversation."""
    payload = store.get_agent_turn_input(execution.execution_id) or {}
    return bool((payload.get("request") or {}).get("scheduled_task"))


def _speaker(store, execution_id) -> object:
    payload = store.get_agent_turn_input(execution_id) or {}
    return (payload.get("request") or {}).get("speaker_id")


def _part_of_automatic_work(store, execution_id) -> bool:
    """A check-in or scheduled run, or something one of them started."""
    seen = set()
    while execution_id and execution_id not in seen:
        seen.add(execution_id)
        if _speaker(store, execution_id) in _AUTOMATIC_SPEAKERS:
            return True
        execution = store.get_execution(execution_id)
        execution_id = execution.parent_execution_id if execution is not None else None
    return False


def is_check_in(store, execution_id) -> bool:
    """An always-on check-in: cheap to repeat, so a restart never continues it."""
    return _speaker(store, execution_id) == _CHECK_IN_SPEAKER


def _ancestors(store, execution) -> list:
    """The runs above this one, nearest first.

    A Retry or Create branch starts from a saved point of its source and is a
    run of its own, so the chain stops at the branch.
    """
    found = []
    seen = {execution.execution_id}
    current = execution
    while (current.parent_execution_id and not current.source_checkpoint_id
           and current.parent_execution_id not in seen):
        seen.add(current.parent_execution_id)
        current = store.get_execution(current.parent_execution_id)
        if current is None:
            break
        found.append(current)
    return found


def _nobody_is_waiting_for(runner, execution) -> bool:
    """A child Job started for a caller that is gone, with no run above it left.

    Only a Job its caller waits on qualifies: one started in the background
    reports on its own when it finishes, and outlives its parent by design.
    """
    store = runner._execution_store
    if not execution.parent_execution_id or store.get_job_agent_input(execution.execution_id) is None:
        return False
    if any(ancestor.status not in TERMINAL_EXECUTION_STATUSES for ancestor in _ancestors(store, execution)):
        return False
    job = runner.get_job(execution.execution_id)
    return job is not None and job.wait


def _nobody_can_continue(runner, execution) -> bool:
    """A turn no user can reach to continue by hand, or whose result nobody would read."""
    store = runner._execution_store
    return (_is_scheduled_run(store, execution) or is_check_in(store, execution.execution_id)
            or _nobody_is_waiting_for(runner, execution))


def _give_up(runner, execution, event, outcome):
    """No restart will continue this turn.

    A chat's turn stays paused for the user to continue by hand. No one can
    reach a scheduled run's or a check-in's, and a waited-on child's caller is
    gone, so these end here; for a scheduled run that ending is what tells the
    task page and the user.
    """
    store = runner._execution_store
    not_continued(store, execution)
    # Before the transaction: a Job's record sits behind the session lock, and
    # admission takes that lock before this store's own.
    ends_here = execution.status is ExecutionStatus.PAUSED and _nobody_can_continue(runner, execution)
    with store._transaction() as connection:
        store._append_event(
            connection,
            execution_id=execution.execution_id,
            execution_version=execution.status_version,
            kind=_SETTLED,
            payload=dict(request_sequence=event.sequence, outcome=outcome),
            created_at=time(),
        )
        if ends_here:
            store._transition_execution(
                connection, execution.execution_id, expected_version=execution.status_version,
                target=ExecutionStatus.INTERRUPTED, reason_code=RESTART_NOT_CONTINUED,
            )


def _fallback_after_contract_mismatch(store, execution, event, command):
    """A rejected immutable checkpoint can request a fresh chat, never tool replay."""
    payload = store.get_agent_turn_input(execution.execution_id)
    if (not payload or payload.get("kind") != "chat"
            or payload.get("request", {}).get("source") not in {"web", "tui", "acp"}):
        return False
    with store._transaction() as connection:
        if connection.exists(
            "execution_events", execution_id=execution.execution_id, kind=_SETTLED,
            where=lambda row: _request_sequence(row) == event.sequence,
        ):
            return True
        current = store._require_execution(connection, execution.execution_id)
        if (current.status is not ExecutionStatus.PAUSED or current.current_attempt_id
                or current.reason_code != "continuation_contract_mismatch"
                or current.status_version != command.result_version):
            return False
        store._append_event(connection, execution_id=current.execution_id,
            execution_version=current.status_version, kind=_REQUEST,
            payload=dict(event.payload, mode="new_turn", expected_version=current.status_version,
                         pause_command_id=None), created_at=time())
        store._append_event(connection, execution_id=current.execution_id,
            execution_version=current.status_version, kind=_SETTLED,
            payload=dict(request_sequence=event.sequence, outcome="checkpoint_incompatible"), created_at=time())
    return True


def stopped_by_a_restart(store, execution) -> bool:
    return execution.reason_code in _PAUSED_BY_RESTART or any(
        command.command_id.startswith(_SHUTDOWN_PAUSE)
        and command.status is CommandStatus.APPLIED
        and command.result_version == execution.status_version
        for command in store.list_commands(execution.execution_id)
    )


def _has_open_intent(store, execution) -> bool:
    events = store.list_events(execution.execution_id)
    settled = {e.payload.get("request_sequence") for e in events if e.kind == _SETTLED}
    return any(e.kind == _REQUEST and e.sequence not in settled for e in events)


def _end_stranded_runs(runner) -> None:
    """End what an earlier restart paused and no intent will ever pick up.

    Before intents reached the log, each restart forgot its own. A chat stays
    paused for the user; nobody can reach these.
    """
    store = runner._execution_store
    ended = 0
    for execution in store.list_nonterminal():
        try:
            if (execution.status is not ExecutionStatus.PAUSED or execution.current_attempt_id
                    or not _nobody_can_continue(runner, execution)
                    or not stopped_by_a_restart(store, execution)
                    or _has_open_intent(store, execution)):
                continue
            with store._transaction() as connection:
                store._transition_execution(
                    connection, execution.execution_id, expected_version=execution.status_version,
                    target=ExecutionStatus.INTERRUPTED, reason_code=RESTART_NOT_CONTINUED,
                )
            ended += 1
        except Exception:  # noqa: BLE001 - one run must not keep the others stranded
            _log.exception("could not end stranded run %s", execution.execution_id)
    if ended:
        _log.info("ended %d run(s) an earlier restart left paused", ended)


def reconcile(runner) -> None:
    """Resume the exact restart-owned pause, honoring any explicit deadline."""
    from openprogram.operations.self_update.control.maintenance import maintenance_blocks

    if getattr(runner, "_restart_shutdown", False) or maintenance_blocks(
        "worker_restart"
    ):
        return
    store = runner._execution_store
    if not getattr(runner, "_stranded_runs_ended", False):
        runner._stranded_runs_ended = True
        _end_stranded_runs(runner)
    from contextlib import closing
    with closing(store._connect()) as connection:
        # Include interrupted frames until their durable new-turn request settles.
        settled = {
            (row["execution_id"], _request_sequence(row))
            for row in connection.rows("execution_events", kind=_SETTLED)
        }
        ids = list(dict.fromkeys(
            row["execution_id"]
            for row in connection.rows("execution_events", kind=_REQUEST, order="sequence")
            if (row["execution_id"], row["sequence"]) not in settled
        ))
    for execution_id in ids:
        execution = store.get_execution(execution_id)
        if (
            execution is None or execution.current_attempt_id
        ):
            continue
        events = store.list_events(execution.execution_id)
        requests = [e for e in events if e.kind == _REQUEST]
        if not requests:
            continue
        event = requests[-1]
        answered = {e.payload.get("request_sequence") for e in events if e.kind == _SETTLED}
        # A shutdown that died before its pause took hold left an intent the
        # next Worker's recovery wrote over.
        for replaced in requests[:-1]:
            if replaced.sequence not in answered:
                _settle(store, execution, replaced, "replaced")
        if event.sequence in answered:
            continue
        data = event.payload
        # A check-in is cheap to repeat: a stale one must neither go on acting
        # nor start over, and it is admitted as a web turn, so a new-turn
        # intent reaches it too.
        if (data.get("mode") == "new_turn" or execution.status is ExecutionStatus.PAUSED) and is_check_in(
            store, execution.execution_id
        ):
            _give_up(runner, execution, event, "check_in_not_resumed")
            continue
        if data.get("mode") == "new_turn":
            from .hooks import start_recovery_turn
            try:
                outcome = start_recovery_turn(store, execution, event)
                if outcome:
                    if outcome != "new_turn_admitted":
                        not_continued(store, store.get_execution(execution.execution_id))
                    _settle(store, store.get_execution(execution.execution_id), event, outcome)
            except Exception:
                _log.exception("new-turn recovery failed for %s", execution.execution_id)
            continue
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            _settle(store, execution, event, "execution_ended")
            continue
        if execution.status is not ExecutionStatus.PAUSED:
            continue
        command_id = (
            f"worker-restart:continue:{execution.execution_id}:{event.sequence}"
        )
        existing = store.get_command(command_id)
        if existing is not None:
            if (existing.status is CommandStatus.REJECTED
                    and existing.rejection_code == "continuation_contract_mismatch"
                    and _fallback_after_contract_mismatch(store, execution, event, existing)):
                continue
            if existing.status is CommandStatus.REJECTED:
                _give_up(runner, execution, event, existing.status.value)
            elif existing.status is CommandStatus.APPLIED:
                _settle(store, execution, event, existing.status.value)
            continue
        if _nobody_is_waiting_for(runner, execution):
            _give_up(runner, execution, event, "nobody_waiting")
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
            _give_up(runner, execution, event, "execution_changed")
            continue
        if (
            time() < data["interrupted_at"]
            or (data["resume_before"] is not None and time() > data["resume_before"])
            or window_seconds() == 0
        ):
            _give_up(runner, execution, event, "expired")
            continue
        # The user has moved on: a later turn of this conversation started
        # after the interruption, so continuing this one now would act out
        # of turn. It stays paused for the user to continue by hand. A
        # scheduled run is on its own branch and cannot be out of turn.
        if not _is_scheduled_run(store, execution) and _superseded(store, execution, data["interrupted_at"]):
            not_continued(store, execution)
            _settle(store, execution, event, "superseded")
            continue
        service = _service(runner, execution.execution_id)
        if service.effects.list_unresolved(execution.execution_id):
            continue
        if _conversation_is_busy(store, execution):
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
