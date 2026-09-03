"""Dispatch one frozen verifier Job after the supervisor releases system gates."""

from __future__ import annotations

import logging
import math
import os
import time

from .store import SelfUpdateStore
from .types import TERMINAL_PHASES, UpdatePhase
from .verifier_config import load_verifier_config, verifier_prompt
from .rollback_intent import load_rollback_intent


SYSTEM_CHECKS = frozenset({"runtime_revision", "owner_auth", "health", "web", "websocket", "doctor"})
_STARTUP_SECONDS = 90
_CLAIM_SECONDS = 15
_log = logging.getLogger(__name__)


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
        or type(gate["worker_pid"]) is not int or gate["worker_pid"] != os.getpid()
        or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
        or not math.isfinite(stamp) or stamp < record.request.created_at
        or not 0 <= time.time() - stamp <= _STARTUP_SECONDS
        or not isinstance(gate["checks"], dict) or set(gate["checks"]) != SYSTEM_CHECKS
        or not all(value is True for value in gate["checks"].values())
    ):
        raise ValueError("system gate did not pass for this worker and attempt")


def require_verifier_execution(*, session_id, spawn_caller, **inputs) -> None:
    """Check restored Jobs at execution, not only at startup admission."""
    from openprogram.agent.run_control import get_current_execution_id

    store = SelfUpdateStore()
    with store._locked():
        record = store._load_active_unlocked()
        if record is None or record.state.phase is not UpdatePhase.VERIFYING:
            raise ValueError("verifier execution requires an active verifying update")
        if load_rollback_intent(store, record) is not None:
            raise ValueError("verifier execution is forbidden during rollback")
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
        expected["prompt"] = verifier_prompt(record)
        if inputs != expected:
            raise ValueError("verifier execution inputs differ from the frozen request")


def recover_pending_updates() -> bool:
    """Return whether ordinary Scheduler startup may proceed.

    Web must already be serving: the installer waits for health before the
    external supervisor can write VERIFYING with its system-gate receipt.
    No App mutation or ordinary Scheduler task is performed here.
    """
    from openprogram.agent.job import get_runner
    from openprogram.agent.job.store import load_job
    from openprogram.agent.internals._model_tools import resolve_model

    store = SelfUpdateStore()
    record = None
    try:
        record = store.load_active()
        if record is None or record.state.phase in {
            UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY,
        }:
            return True
        deadline = time.monotonic() + min(
            _STARTUP_SECONDS,
            max(0, record.request.created_at + record.request.timeout_seconds - time.time()),
        )
        rollback_deadline = None
        while True:
            record = store.load(record.request.update_id)
            if record.state.phase in TERMINAL_PHASES:
                return record.state.phase is not UpdatePhase.NEEDS_MANUAL_RECOVERY
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
            if time.monotonic() >= deadline:
                raise ValueError("startup verification handoff timed out")
            error_path = store.root / record.request.update_id / f"startup-error-{record.state.attempt}.json"
            if error_path.exists() or error_path.is_symlink():
                return False
            if record.state.phase is UpdatePhase.ACTIVATING:
                time.sleep(0.1)
                continue
            if record.state.phase is not UpdatePhase.VERIFYING:
                raise ValueError("unexpected startup update phase")
            _check_gate(record)
            config = load_verifier_config(store, record)
            job_id = f"self-update:{record.request.update_id}:verify:{record.state.attempt}"
            existing = load_job(record.request.session_id, job_id)
            if existing is not None:
                if existing.source != "self_update_verify" or existing.agent_id != record.request.agent_id:
                    raise ValueError("verifier Job ID is occupied by a different job")
                return True  # Terminal/orphan Jobs are evidence, never resubmitted.
            resolve_model(config["profile_snapshot"], config["model_override"])
            claim = store.claim_verifier(
                record.request.update_id, owner=f"worker:{os.getpid()}", lease_seconds=_CLAIM_SECONDS,
            )
            if not claim.acquired:
                time.sleep(0.1)
                continue
            runner = get_runner()
            with store._locked():
                current = store._load_unlocked(record.request.update_id)
                if current.state.phase in TERMINAL_PHASES:
                    return current.state.phase is not UpdatePhase.NEEDS_MANUAL_RECOVERY
                if load_rollback_intent(store, current) is not None:
                    continue
                dispatch = current.state.dispatch
                if current.state.phase is not UpdatePhase.VERIFYING or dispatch is None:
                    raise ValueError("verification phase changed before admission")
                if dispatch.generation != claim.generation or dispatch.lease_until <= time.time():
                    continue
                # Serialize admission with supervisor rollback. spawn_job does
                # not wait for execution; its turn admission resumes on unlock.
                _check_gate(current)
                runner.spawn_job(
                    job_id=claim.job_id, session_id=record.request.session_id,
                    prompt=verifier_prompt(record), agent_id=config["agent_id"],
                    source="self_update_verify", context_mode="clean", parent_msg_id=None,
                    caller_msg_id=record.request.origin_assistant_id,
                    spawn_caller=record.request.origin_assistant_id, advance_head=False,
                    wait=True, label="Post-update verification", creates_agent=False,
                    profile_snapshot=config["profile_snapshot"], model_override=config["model_override"],
                    tools_override=config["tools_override"], response_format=config["response_format"],
                    authority=config["authority"],
                )
            return True
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
