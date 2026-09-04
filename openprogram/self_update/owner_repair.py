"""Explicit, bounded owner recovery through the original trusted controller."""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import time
import uuid

from .store import SelfUpdateStore
from .types import TERMINAL_PHASES, UpdatePhase
from .maintenance import load_maintenance, _leave_maintenance_unlocked
from .verification_channel import _read, _digest

REPAIR_SECONDS = 600
_OLD_PHASES = ("prepared", "activating", "activated", "rolling_back", "rolled_back")


def _owner(store, record):
    from openprogram.paths import get_active_profile
    if get_active_profile() is not None:
        raise ValueError("self-update repair requires the default profile")
    for directory in (store.root, store.root / record.request.update_id):
        info = directory.lstat()
        if directory.is_symlink() or not directory.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("self-update state is not owned privately by this local user")


def _plan(store, record):
    from .controller_bundle import _load_bundle
    from .commit_intent import load_commit, read_journal
    from .rollback_intent import load_rollback_intent
    from .supervisor import _validate_transaction_path
    _owner(store, record)
    marker = load_maintenance(store)
    if record.state.phase not in TERMINAL_PHASES or marker is None or marker["update_id"] != record.request.update_id:
        raise ValueError("repair requires this terminal update's maintenance ownership")
    if store._load_active_unlocked() is not None:
        raise ValueError("another update is active")
    bundle = _load_bundle(store.root / record.request.update_id / "controller")
    transaction = _validate_transaction_path(Path(record.state.detail["transaction_dir"]))
    journal = read_journal(transaction)
    intent = load_rollback_intent(store, record)
    proof = intent
    revision = record.state.detail.get("previous_system_gate", {}).get("candidate_sha")
    if record.state.phase is UpdatePhase.ABORTED:
        if journal["phase"] != "prepared":
            raise ValueError("aborted transaction is no longer prepared")
        action, revision = "unchanged-old", None
    elif journal["phase"] in {"committing", "committed"}:
        if record.state.phase not in {UpdatePhase.SUCCEEDED, UpdatePhase.NEEDS_MANUAL_RECOVERY} or intent is not None:
            raise ValueError("committed transaction conflicts with the original outcome")
        proof, journal = load_commit(store, record, transaction, bundle.installer_sha256)
        action, revision = "accepted-candidate", record.request.candidate_sha
    else:
        if record.state.phase not in {UpdatePhase.ROLLED_BACK, UpdatePhase.NEEDS_MANUAL_RECOVERY} or journal["phase"] not in _OLD_PHASES:
            raise ValueError("transaction cannot restore the previous App")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", revision):
            raise ValueError("previous runtime revision is unknown")
        action = "restored-old"
    return dict(schema=1, update_id=record.request.update_id, phase=record.state.phase.value,
                request_sha256=_digest(record.request.to_dict()), state_sha256=_digest(record.state.to_dict()),
                maintenance_sha256=_digest(marker), transaction_dir=str(transaction),
                transaction_identity={k: v for k, v in journal.items() if k != "phase"},
                initial_phase=journal["phase"], action=action, target_revision=revision,
                proof_sha256=_digest(proof), installer_sha256=bundle.installer_sha256,
                runtime_sha256=bundle.runtime_sha256)


def preview_repair(update_id: str) -> dict:
    store = SelfUpdateStore()
    with store._locked():
        return _plan(store, store._load_unlocked(update_id))


def load_repair(store, record) -> dict | None:
    directory = store.root / record.request.update_id
    try:
        pointer = _read(directory / "owner-repair.json")
    except FileNotFoundError:
        return None
    repair_id = pointer.get("repair_id")
    if set(pointer) != {"schema", "repair_id"} or type(pointer["schema"]) is not int or pointer["schema"] != 1 or not isinstance(repair_id, str) or not re.fullmatch(r"[0-9a-f]{32}", repair_id):
        raise ValueError("invalid owner repair pointer")
    request = _read(directory / f"owner-repair-{repair_id}.json")
    if (set(request) != {"schema", "repair_id", "update_id", "owner_uid", "created_at", "deadline", "plan"}
        or type(request["schema"]) is not int or request["schema"] != 1
        or request["repair_id"] != repair_id or request["update_id"] != record.request.update_id
        or type(request["owner_uid"]) is not int or request["owner_uid"] != os.getuid()
        or any(type(request[k]) not in (int, float) or not math.isfinite(request[k]) for k in ("created_at", "deadline"))
        or not record.request.created_at <= request["created_at"] <= time.time()
        or request["deadline"] != request["created_at"] + REPAIR_SECONDS
        or not isinstance(request["plan"], dict)):
        raise ValueError("invalid owner repair request")
    return request


def read_result(store, request) -> dict | None:
    path = store.root / request["update_id"] / f"owner-repair-result-{request['repair_id']}.json"
    try:
        value = _read(path)
    except FileNotFoundError:
        return None
    if (set(value) != {"schema", "repair_id", "update_id", "request_sha256", "status", "at", "resolution", "system_gate", "error"}
        or type(value["schema"]) is not int or value["schema"] != 1 or value["repair_id"] != request["repair_id"]
        or value["update_id"] != request["update_id"] or value["request_sha256"] != _digest(request)
        or value["status"] not in {"recovered", "failed"}
        or type(value["at"]) not in (int, float) or not math.isfinite(value["at"])
        or not request["created_at"] <= value["at"] <= time.time()):
        raise ValueError("invalid owner repair result")
    if value["status"] == "recovered":
        from .recovery import SYSTEM_CHECKS
        gate = value["system_gate"]
        expected_revision = request["plan"]["target_revision"]
        if (value["at"] >= request["deadline"] or value["resolution"] != request["plan"]["action"]
            or not isinstance(gate, dict) or gate.get("checks") != {k: True for k in SYSTEM_CHECKS}
            or (expected_revision is not None and gate.get("candidate_sha") != expected_revision)
            or not request["created_at"] <= gate.get("verified_at", 0) <= value["at"]):
            raise ValueError("owner repair success lacks valid system evidence")
    return value


def cleanup_error(store, request) -> dict | None:
    path = store.root / request["update_id"] / f"owner-repair-cleanup-error-{request['repair_id']}.json"
    try:
        value = _read(path)
    except FileNotFoundError:
        return None
    if (set(value) != {"request_sha256", "at", "error"} or value["request_sha256"] != _digest(request)
        or not isinstance(value["error"], str) or type(value["at"]) not in (int, float)
        or not math.isfinite(value["at"]) or not request["created_at"] <= value["at"] <= time.time()):
        raise ValueError("invalid owner repair cleanup error")
    return value


def _write_result(store, request, *, gate=None, error=""):
    value = dict(schema=1, repair_id=request["repair_id"], update_id=request["update_id"],
                 request_sha256=_digest(request), status="failed" if error else "recovered", at=time.time(),
                 resolution=request["plan"]["action"], system_gate=gate, error=error[:1000])
    store._write_json(store.root / request["update_id"] / f"owner-repair-result-{request['repair_id']}.json", value)
    return value


def approve_repair(update_id: str, plan_sha256: str) -> dict:
    from .supervisor import _controller_lock
    store = SelfUpdateStore()
    record = store.load(update_id)
    with _controller_lock(store.root / update_id) as acquired:
        if not acquired:
            raise ValueError("the update controller is still running")
        with store._locked():
            record = store._load_unlocked(update_id)
            plan = _plan(store, record)
            if _digest(plan) != plan_sha256:
                raise ValueError("recovery plan changed; inspect and confirm again")
            prior = load_repair(store, record)
            if prior is not None and read_result(store, prior) is None:
                if time.time() < prior["deadline"]:
                    raise ValueError("an approved repair is already pending")
                _write_result(store, prior, error="repair authorization expired")
            now = time.time()
            request = dict(schema=1, repair_id=uuid.uuid4().hex, update_id=update_id,
                           owner_uid=os.getuid(), created_at=now, deadline=now + REPAIR_SECONDS, plan=plan)
            directory = store.root / update_id
            store._write_json(directory / f"owner-repair-{request['repair_id']}.json", request)
            store._write_json(directory / "owner-repair.json", dict(schema=1, repair_id=request["repair_id"]))
            return request


def fail_before_launch(store, update_id, repair_id, error):
    """End a deterministically rejected launch without revoking a live controller."""
    from .supervisor import _controller_lock
    with _controller_lock(store.root / update_id) as acquired:
        if not acquired:
            return
        with store._locked():
            request = load_repair(store, store._load_unlocked(update_id))
            if request is None or request["repair_id"] != repair_id:
                return
            result = read_result(store, request)
            if result is None:
                _write_result(store, request, error=error)
            elif result["status"] == "recovered" and load_maintenance(store) is not None:
                store._write_json(store.root / update_id / f"owner-repair-cleanup-error-{repair_id}.json", {
                    "request_sha256": _digest(request), "at": time.time(), "error": error,
                })


def status(update_id: str | None = None) -> dict:
    store = SelfUpdateStore()
    if not store.root.exists():
        return {"update_id": None}
    with store._locked():
        marker = load_maintenance(store)
        active = store._load_active_unlocked()
        if update_id is None:
            update_id = marker["update_id"] if marker else active.request.update_id if active else None
        if update_id is None:
            return {"update_id": None}
        record = store._load_unlocked(update_id)
        return _status_unlocked(store, record)


def _status_unlocked(store, record) -> dict:
    """Describe an already loaded record without reconciling the active pointer."""
    _owner(store, record)
    marker = load_maintenance(store)
    request = load_repair(store, record)
    from .diagnosis import _result
    from .source_repair import read_result as source_repair_result
    update_id = record.request.update_id
    script = store.root / update_id / "recover.sh"
    return dict(update_id=update_id, phase=record.state.phase.value,
                recovery_script=str(script) if script.is_file() and not script.is_symlink() else None,
                maintenance=marker is not None and marker["update_id"] == update_id,
                repair_id=request["repair_id"] if request else None,
                repair_deadline=request["deadline"] if request else None,
                repair_result=read_result(store, request) if request else None,
                repair_cleanup_error=cleanup_error(store, request) if request else None,
                diagnosis_result=_result(store, record), source_repair_result=source_repair_result(store, record))


def run_repair(store, record, installer_sha256: str) -> int | None:
    """Called only while the public supervisor owns its per-update lock."""
    from . import supervisor
    from .system_probe import _probe_system, SystemProbeError
    request = load_repair(store, record)
    if request is None:
        return None
    result = read_result(store, request)
    if result is not None:
        if result["status"] == "failed" or cleanup_error(store, request) is not None:
            return 1
        if load_maintenance(store) is None:
            return 0
    deadline = time.monotonic() + max(0, request["deadline"] - time.time())
    def remaining():
        value = min(deadline - time.monotonic(), request["deadline"] - time.time())
        if value <= 0:
            raise ValueError("repair authorization expired")
        return value
    def validate():
        current = store._load_unlocked(record.request.update_id)
        plan = _plan(store, current)
        expected = request["plan"]
        if (installer_sha256 != expected["installer_sha256"]
            or {k: v for k, v in plan.items() if k != "initial_phase"} != {k: v for k, v in expected.items() if k != "initial_phase"}):
            raise ValueError("approved recovery evidence changed")
        phases = _OLD_PHASES if expected["action"] == "restored-old" else ("committing", "committed") if expected["action"] == "accepted-candidate" else ("prepared",)
        if plan["initial_phase"] not in phases or phases.index(plan["initial_phase"]) < phases.index(expected["initial_phase"]):
            raise ValueError("recovery transaction phase regressed")
        return current, plan
    try:
        remaining()
        with store._locked():
            record, plan = validate()
        transaction = Path(plan["transaction_dir"])
        def command(mode, timeout):
            reported = supervisor._installer_command(transaction, store.root / record.request.update_id,
                                                     installer_sha256, mode, timeout_seconds=min(timeout, remaining()))
            if reported != str(transaction):
                raise ValueError("repair installer reported another transaction")
            remaining()
        terminal = {"restored-old": "rolled_back", "accepted-candidate": "committed", "unchanged-old": "prepared"}[plan["action"]]
        if result is None:
            if plan["action"] == "restored-old":
                command("--rollback", 300)
            elif plan["action"] == "accepted-candidate":
                command("--commit", 300)
            command("--restart-terminal:" + terminal, 180)
        # WorkerLock publication precedes provider/Web/frontend readiness.
        probe_deadline = time.monotonic() + min(60, remaining())
        while True:
            try:
                gate = _probe_system(record, plan["target_revision"], min(remaining(), probe_deadline - time.monotonic()))
                break
            except SystemProbeError:
                wait = min(remaining(), probe_deadline - time.monotonic())
                if wait <= 0:
                    raise
                time.sleep(min(0.2, wait))
        if plan["action"] == "unchanged-old" and gate["candidate_sha"] == record.request.candidate_sha:
            raise ValueError("aborted update is running the candidate")
        command("--verify-terminal:" + terminal, 30)
        with store._locked():
            validate()
            if load_repair(store, record) != request:
                raise ValueError("repair request changed during execution")
            remaining()
            if result is None:
                result = _write_result(store, request, gate=gate)
            store._write_json(store.root / record.request.update_id / f"owner-repair-cleanup-{request['repair_id']}.json", {
                "schema": 1, "request_sha256": _digest(request), "system_gate": gate, "at": time.time(),
            })
            _leave_maintenance_unlocked(store, record.request.update_id)
        return 0
    except Exception as exc:
        with store._locked():
            if result is None:
                _write_result(store, request, error=str(exc) or type(exc).__name__)
            else:
                store._write_json(store.root / request["update_id"] / f"owner-repair-cleanup-error-{request['repair_id']}.json", {
                    "request_sha256": _digest(request), "at": time.time(), "error": str(exc) or type(exc).__name__,
                })
        return 1
