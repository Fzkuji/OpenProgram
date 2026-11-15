"""Dispatch one frozen verifier Job after the supervisor releases system gates."""

from __future__ import annotations

import logging
import math
import os
import time

from ..store import SelfUpdateStore
from ..types import TERMINAL_PHASES
from ..types import UpdatePhase
from ..types import VERIFIER_PHASES
from ..verification.verifier_config import load_verifier_config
from ..verification.verifier_config import verifier_prompt
from ..verification.verifier_config import validate_model_capabilities
from .rollback_intent import load_rollback_intent


SYSTEM_CHECKS = frozenset({"runtime_revision", "owner_auth", "health", "web", "websocket", "doctor"})
_STARTUP_SECONDS = 90
_CLAIM_SECONDS = 15
_log = logging.getLogger(__name__)


def _expected_gate_pid(record) -> int:
    """Which worker the receipt names: this one, or the trial's it observes."""
    if record.state.phase is UpdatePhase.TRIAL:
        return record.state.detail.get("trial", {}).get("worker_pid")
    return os.getpid()


def _check_gate(record) -> None:
    gate = record.state.detail.get("system_gate")
    if not isinstance(gate, dict) or set(gate) != {
        "schema", "candidate_sha", "attempt", "verified_at", "worker_pid", "checks",
    }:
        raise ValueError("system gate receipt is missing or malformed")
    stamp = gate["verified_at"]
    if (
        type(gate["schema"]) is not int or gate["schema"] != 1
        or gate["candidate_sha"] != record.request.candidate_sha
        or type(gate["attempt"]) is not int or gate["attempt"] != record.state.attempt
        or type(gate["worker_pid"]) is not int or gate["worker_pid"] != _expected_gate_pid(record)
        or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
        or not math.isfinite(stamp) or stamp < record.request.created_at
        or not 0 <= time.time() - stamp <= _STARTUP_SECONDS
        or not isinstance(gate["checks"], dict) or set(gate["checks"]) != SYSTEM_CHECKS
        or not all(value is True for value in gate["checks"].values())
    ):
        raise ValueError("system gate did not pass for this worker and attempt")


def require_verifier_execution(*, session_id, spawn_caller, **inputs) -> None:
    """Check restored Jobs at execution, not only at startup admission."""
    from openprogram.runtime.execution.run_control import get_current_execution_id
    from .commit_intent import commit_pending

    store = SelfUpdateStore()
    with store._locked():
        record = store._load_active_unlocked()
        if record is None or record.state.phase not in VERIFIER_PHASES:
            raise ValueError("verifier execution requires an active update in trial or verifying")
        if load_rollback_intent(store, record) is not None:
            raise ValueError("verifier execution is forbidden during rollback")
        if commit_pending(store, record):
            raise ValueError("verifier execution is forbidden during commit")
        _check_gate(record)
        dispatch = record.state.dispatch
        job_id = f"self-update:{record.request.update_id}:verify:{record.state.attempt}"
        if (
            dispatch is None or dispatch.job_id != job_id
            or get_current_execution_id() != job_id
            or dispatch.claimed_by != f"worker:{os.getpid()}"
            or dispatch.lease_until <= time.time()
            or time.time() >= record.request.created_at + record.request.timeout_seconds
            or session_id != record.request.session_id
            or spawn_caller != record.request.origin_assistant_id
        ):
            raise ValueError("verifier execution does not own the current startup claim")
        error_path = store.root / record.request.update_id / f"startup-error-{record.state.attempt}.json"
        if error_path.exists() or error_path.is_symlink():
            raise ValueError("verifier startup already failed")
        config = load_verifier_config(store, record)
        expected = {key: config[key] for key in (
            "agent_id", "profile_snapshot", "model_override", "tools_override", "response_format", "authority",
        )}
        expected["prompt"] = verifier_prompt(record, config)
        if inputs != expected:
            raise ValueError("verifier execution inputs differ from the frozen request")
        validate_model_capabilities(config)


def recover_pending_updates() -> bool:
    """Return whether ordinary Scheduler startup may proceed.

    Web must already be serving: the installer waits for health before the
    external supervisor can write VERIFYING with its system-gate receipt.
    No App mutation or ordinary Scheduler task is performed here.
    """
    from ..delivery.launcher import launch_supervisor
    from .commit_intent import commit_pending
    from .rollback_intent import RECOVERY_SECONDS
    from .maintenance import load_maintenance
    from ..repair.owner_repair import load_repair
    from ..repair.owner_repair import read_result
    from ..repair.owner_repair import cleanup_error

    store = SelfUpdateStore()
    record = None
    try:
        from ..repair.next_candidate import dispatch_pending as dispatch_next
        dispatch_next()
        started = time.time()
        with store._locked():
            record = store._load_active_unlocked()
            marker = load_maintenance(store)
            if marker is not None:
                if record is not None and record.request.update_id != marker["update_id"]:
                    raise ValueError("maintenance conflicts with the active update")
                record = store._load_unlocked(marker["update_id"])
        if record is None:
            from ..repair.diagnosis import dispatch_pending
            dispatch_pending()
            from ..repair.source_repair import dispatch_pending as dispatch_repair
            dispatch_repair()
            from .switch import reconcile_at_start
            reconcile_at_start(store, None)
            return True
        from . import switch
        if switch.owns(record):
            # The old version's controller owns this update; the worker only
            # restarts it, or records a rollback the launcher made alone.
            switch.reconcile_at_start(store, record)
            from ..repair.diagnosis import dispatch_pending
            dispatch_pending()
            return True
        from . import dev_stable
        if dev_stable.owns(record):
            # Its controller works outside this worker and restarts it on
            # purpose; turns keep running on whichever code is installed.
            dev_stable.ensure_running(store, record)
            return True
        repair = load_repair(store, record)
        if record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY and repair is None:
            return False
        launch_supervisor(record.request.update_id, resume=True)
        record = store.load(record.request.update_id)
        if record.state.phase in {
            UpdatePhase.PREPARING, UpdatePhase.BUILDING, UpdatePhase.READY,
        }:
            error_path = store.root / record.request.update_id / f"startup-error-{record.state.attempt}.json"
            return not (error_path.exists() or error_path.is_symlink())
        deadline = time.monotonic() + min(
            _STARTUP_SECONDS,
            max(0, record.request.created_at + record.request.timeout_seconds - time.time()),
        )
        rollback_deadline = None
        commit_deadline = time.monotonic() + RECOVERY_SECONDS
        repair_deadline = time.monotonic() + max(0, repair["deadline"] - time.time()) if repair else None
        while True:
            record = store.load(record.request.update_id)
            if repair is not None:
                if load_repair(store, record) != repair:
                    raise ValueError("owner repair changed during startup")
                result = read_result(store, repair)
                if cleanup_error(store, repair) is not None or (result is not None and result["status"] == "failed"):
                    return False
                if result is not None and load_maintenance(store) is None:
                    return True
                if time.monotonic() >= repair_deadline:
                    raise ValueError("owner repair startup handoff timed out")
                time.sleep(0.1)
                continue
            if record.state.phase in TERMINAL_PHASES:
                if record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY:
                    return False
                marker = load_maintenance(store)
                if marker is None:
                    from ..repair.diagnosis import dispatch_pending
                    dispatch_pending()
                    from ..repair.source_repair import dispatch_pending as dispatch_repair
                    dispatch_repair()
                    return True
                if marker["update_id"] != record.request.update_id:
                    raise ValueError("terminal maintenance owner changed")
                error_path = store.root / record.request.update_id / f"maintenance-error-{record.state.attempt}.json"
                if error_path.exists() or error_path.is_symlink():
                    from ..verification.verification_channel import _read
                    if _read(error_path).get("at", 0) >= started:
                        return False
                if time.monotonic() >= commit_deadline:
                    raise ValueError("terminal maintenance cleanup timed out")
                time.sleep(0.1)
                continue
            intent = load_rollback_intent(store, record)
            if intent is not None:
                if rollback_deadline is None:
                    rollback_deadline = time.monotonic() + max(0, intent["deadline"] - time.time())
                if time.monotonic() >= rollback_deadline:
                    raise ValueError("startup rollback handoff timed out")
                # Both candidate and restored workers wait for the controller's
                # fresh old-version checks, even after a candidate startup error.
                time.sleep(0.1)
                continue
            if commit_pending(store, record):
                if time.monotonic() >= commit_deadline:
                    raise ValueError("startup commit reconciliation timed out")
                time.sleep(0.1)
                continue
            if time.monotonic() >= deadline:
                raise ValueError("startup verification handoff timed out")
            error_path = store.root / record.request.update_id / f"startup-error-{record.state.attempt}.json"
            if error_path.exists() or error_path.is_symlink():
                return False
            if record.state.phase is UpdatePhase.ACTIVATING:
                time.sleep(0.1)
                continue
            if record.state.phase not in VERIFIER_PHASES:
                raise ValueError("unexpected startup update phase")
            if _admit_verifier(store, record):
                return True
            time.sleep(0.1)
            continue
    except Exception as exc:
        _log.error("Self-update startup verification failed: %s", type(exc).__name__)
        if record is not None:
            try:
                with store._locked():
                    path = store.root / record.request.update_id / f"startup-error-{record.state.attempt}.json"
                    if not path.exists() and not path.is_symlink():
                        store._write_json(path, {
                            "schema": 1, "update_id": record.request.update_id,
                            "candidate_sha": record.request.candidate_sha, "attempt": record.state.attempt,
                            "worker_pid": os.getpid(), "at": time.time(),
                            "error": str(exc)[:1000] if isinstance(exc, ValueError) else type(exc).__name__,
                        })
            except Exception:
                _log.exception("Could not persist self-update startup failure")
        return False


def _admit_verifier(store, record) -> bool:
    """Claim and spawn the frozen verifier Job; ``False`` means try again."""
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.conversation.model_tools import resolve_model

    _check_gate(record)
    config = load_verifier_config(store, record)
    job_id = f"self-update:{record.request.update_id}:verify:{record.state.attempt}"
    existing = load_job(record.request.session_id, job_id)
    if existing is not None:
        if existing.source != "self_update_verify" or existing.agent_id != record.request.agent_id:
            raise ValueError("verifier Job ID is occupied by a different job")
        return True  # Terminal/orphan Jobs are evidence, never resubmitted.
    validate_model_capabilities(config, resolve_model(config["profile_snapshot"], config["model_override"]))
    claim = store.claim_verifier(
        record.request.update_id, owner=f"worker:{os.getpid()}", lease_seconds=_CLAIM_SECONDS,
    )
    if not claim.acquired:
        return False
    runner = get_runner()
    with store._locked():
        current = store._load_unlocked(record.request.update_id)
        if current.state.phase in TERMINAL_PHASES:
            return False  # Terminal maintenance must also finish before admission.
        if load_rollback_intent(store, current) is not None:
            return False
        dispatch = current.state.dispatch
        if current.state.phase not in VERIFIER_PHASES or dispatch is None:
            raise ValueError("verification phase changed before admission")
        if dispatch.generation != claim.generation or dispatch.lease_until <= time.time():
            return False
        # Serialize admission with supervisor rollback. spawn_job does
        # not wait for execution; its turn admission resumes on unlock.
        _check_gate(current)
        runner.spawn_job(
            job_id=claim.job_id, session_id=record.request.session_id,
            prompt=verifier_prompt(record, config), agent_id=config["agent_id"],
            source="self_update_verify", context_mode="clean", parent_msg_id=None,
            caller_msg_id=record.request.origin_assistant_id,
            spawn_caller=record.request.origin_assistant_id, advance_head=False,
            wait=True, label="Post-update verification", creates_agent=False,
            profile_snapshot=config["profile_snapshot"], model_override=config["model_override"],
            tools_override=config["tools_override"], response_format=config["response_format"],
            authority=config["authority"],
        )
    return True


def _dispatch_trial_verifier(update_id: str) -> bool:
    """Admit the trial's verifier in this, the live, worker."""
    store = SelfUpdateStore()
    deadline = time.monotonic() + _STARTUP_SECONDS
    while time.monotonic() < deadline:
        record = store.load(update_id)
        if record.state.phase is not UpdatePhase.TRIAL:
            raise ValueError("update is no longer in its trial")
        if _admit_verifier(store, record):
            return True
        time.sleep(0.1)
    raise ValueError("the trial's verifier could not be admitted in time")


def dispatch_hot_update(update_id: str) -> bool:
    """Run startup dispatch for a hot-swapped update in this running worker.

    A worker restart admits the verifier, or after a rollback the diagnosis,
    from startup recovery. A hot swap keeps the worker, so the supervisor asks
    for the same dispatch once it has published the phase that needs it.
    """
    from ..delivery.activation import load_plan

    store = SelfUpdateStore()
    record = store.load(update_id)
    if record.state.phase is UpdatePhase.TRIAL:
        return _dispatch_trial_verifier(update_id)
    plan = load_plan(store.root / update_id)
    if plan is None or plan.restart != "hot":
        raise ValueError("update does not keep the running worker")
    active = store.load_active()
    if active is not None and active.request.update_id != update_id:
        raise ValueError("another self-update is active")
    if record.state.phase is not UpdatePhase.VERIFYING and record.state.phase not in TERMINAL_PHASES:
        raise ValueError("update has nothing to dispatch yet")
    return recover_pending_updates()


# Read-only stages run unattended; continuation and replan turns are not here.
_UNATTENDED_STAGES = ("verify", "diagnose", "repair")


def stop_parked_stages(runner) -> list[str]:
    """Cancel read-only stage Jobs whose model request ended without an outcome.

    Such an execution waits in reconciliation_required for someone to settle
    the request, and nobody does for an unattended stage. Cancelling ends its
    Job, so the stage records an inconclusive result now, not at its deadline.
    """
    from openprogram.runtime.jobs.store import load_job
    from openprogram.runtime.jobs.types import TERMINAL_STATUSES
    from openprogram.runtime.execution.model import ExecutionStatus

    from .projection import _records

    store = SelfUpdateStore()
    if not store.root.exists():
        return []
    with store._locked(read_only=True):
        records = list(_records(store))
    stopped = []
    for record in records:
        for stage in _UNATTENDED_STAGES:
            job_id = f"self-update:{record.request.update_id}:{stage}:{record.state.attempt}"
            execution = runner._execution_store.get_execution(job_id)
            if (execution is None or execution.status is not ExecutionStatus.RECONCILIATION_REQUIRED
                    or execution.current_attempt_id is not None):
                continue
            job = load_job(record.request.session_id, job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                continue
            runner.cancel_execution(job_id, reason="model request ended without an outcome")
            stopped.append(job_id)
    return stopped
