"""Read-only, session-scoped status shared by tools and user interfaces."""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path
import re
import time

from .store import READ_SCAN_LIMIT, SelfUpdateStore
from .types import (
    ConcurrentUpdateError, CorruptUpdateStateError, UpdateNotFoundError,
    UpdatePhase, is_terminal, _validate_update_id,
)
from .verification_channel import _check_signature, _digest, _read, _resolve_evidence

MAX_PAGE_SIZE = 50
MAX_RESPONSE_BYTES = 2_097_152


class ProjectionAccessError(ValueError):
    """The requested update does not belong to this session."""


def _session(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("invalid origin session")
    return value


def _optional(path):
    try:
        return _read(path)
    except FileNotFoundError:
        return None


def _runtime(store, record):
    from .recovery import SYSTEM_CHECKS
    from .owner_repair import load_repair, read_result
    from .rollback_intent import load_rollback_intent

    request, state = record.request, record.state
    key = {
        UpdatePhase.PREPARING: "previous_system_gate",
        UpdatePhase.STAGING: "previous_system_gate",
        UpdatePhase.READY: "previous_system_gate",
        UpdatePhase.VERIFYING: "system_gate",
        UpdatePhase.SUCCEEDED: "committed_system_gate",
        UpdatePhase.ROLLED_BACK: "restored_system_gate",
    }.get(state.phase)
    gate = state.detail.get(key) if key else None
    earliest, latest = request.created_at, state.updated_at
    expected = request.candidate_sha if state.phase in {UpdatePhase.VERIFYING, UpdatePhase.SUCCEEDED} else None
    if state.phase is UpdatePhase.ROLLED_BACK:
        intent = load_rollback_intent(store, record)
        if intent is None:
            return None
        expected, earliest = intent["previous_revision"], intent["started_at"]
        latest = min(latest, intent["deadline"])
    # Manual recovery can preserve the original failure phase. Its separate
    # receipt, not an older candidate gate, proves the later observation.
    repair = load_repair(store, record)
    if repair is not None:
        result = read_result(store, repair)
        if result is None or result["status"] != "recovered":
            return None
        gate, key = result["system_gate"], "owner_repair"
        expected, earliest, latest = repair["plan"]["target_revision"], repair["created_at"], result["at"]
    if not isinstance(gate, dict) or set(gate) != {
        "schema", "candidate_sha", "attempt", "worker_pid", "verified_at", "checks",
    }:
        return None
    if (type(gate["schema"]) is not int or gate["schema"] != 1
            or type(gate["attempt"]) is not int or gate["attempt"] != state.attempt
            or type(gate["worker_pid"]) is not int or gate["worker_pid"] <= 0
            or type(gate["verified_at"]) not in (float, int) or not math.isfinite(gate["verified_at"])
            or not earliest <= gate["verified_at"] <= min(latest, time.time())
            or not isinstance(gate["candidate_sha"], str)
            or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", gate["candidate_sha"])
            or expected is not None and gate["candidate_sha"] != expected
            or gate["checks"] != {name: True for name in SYSTEM_CHECKS}
            or any(type(v) is not bool for v in gate["checks"].values())):
        return None
    return {**{k: gate[k] for k in ("candidate_sha", "worker_pid", "verified_at")}, "source": key}


def _verifier(store, record):
    from .verification import validate_verifier_result

    directory = store.root / record.request.update_id
    receipt = _optional(directory / f"verifier-result-{record.state.attempt}.json")
    if receipt is None:
        return None
    grant = _read(directory / f"verifier-grant-{record.state.attempt}.json")
    _check_signature(receipt, grant["token"])
    if (receipt.get("update_id") != record.request.update_id
            or receipt.get("candidate_sha") != record.request.candidate_sha
            or receipt.get("attempt") != record.state.attempt
            or receipt.get("grant_sha256") != _digest(grant)
            or _digest(grant) != record.state.detail.get("verifier_grant_sha256")
            or receipt.get("job_id") != f"self-update:{record.request.update_id}:verify:{record.state.attempt}"
            or receipt.get("verdict") not in {"pass", "fail", "inconclusive"}
            or type(receipt.get("consumed_at")) not in (int, float)
            or not math.isfinite(receipt["consumed_at"])
            or not record.request.created_at <= grant["issued_at"] <= receipt["consumed_at"] <= time.time()):
        raise CorruptUpdateStateError("invalid verifier projection receipt")
    assertions = []
    if receipt.get("result") is not None:
        result = validate_verifier_result(receipt["result"], record.request,
            attempt=record.state.attempt, not_before=grant["issued_at"], now=min(receipt["consumed_at"], grant["deadline"]))
        _resolve_evidence(store, record, grant, result)
        if result.verdict != receipt["verdict"]:
            raise CorruptUpdateStateError("verifier projection verdict differs")
        assertions = [{k: item[k] for k in ("id", "status", "evidence_refs")}
                      for item in result.to_dict()["assertions"]]
    elif receipt["verdict"] != "inconclusive":
        raise CorruptUpdateStateError("verifier projection is missing evidence")
    return dict(verdict=receipt["verdict"], assertions=assertions,
                evidence_id=f"verifier:{record.state.attempt}:{_digest(receipt)}")


def _rollback_available(record, runtime):
    from .commit_intent import read_journal
    from .supervisor import _validate_transaction_path

    if (record.state.phase is not UpdatePhase.VERIFYING or runtime is None
            or record.state.detail.get("rollback_available") is not True):
        return False
    path = record.state.detail.get("transaction_dir")
    if not isinstance(path, str):
        return False
    try:
        transaction = _validate_transaction_path(Path(path))
        journal = read_journal(transaction)
        previous = transaction / "previous.app"
        return journal["phase"] == "activated" and previous.is_dir() and not previous.is_symlink()
    except (OSError, ValueError, RuntimeError):
        return False


def _project(store, record):
    from . import diagnosis, next_candidate, source_repair

    request, state = record.request, record.state
    iteration = next_candidate.summary(store, record, read_only=True)
    if iteration and iteration["submission"]:
        iteration["submission"] = {k: iteration["submission"][k] for k in ("status", "child_id", "at")}
    diagnostic = diagnosis._result(store, record)
    repair = source_repair.read_result(store, record)
    verifier = _verifier(store, record)
    runtime = _runtime(store, record)
    # Free-form error/model output stays in separately authorized evidence,
    # never in a global Running snapshot or an unchecked status tool result.
    def stage(value):
        return None if value is None else {k: value[k] for k in ("status", "at")}

    repair_summary = stage(repair)
    if repair_summary is not None:
        candidate = repair.get("candidate")
        if isinstance(candidate, dict):
            sha = candidate.get("candidate_sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise CorruptUpdateStateError("invalid repaired candidate identity")
            repair_summary["candidate_sha"] = sha
    result = dict(
        update_id=request.update_id, session_id=request.session_id,
        origin_assistant_id=request.origin_assistant_id,
        root_id=iteration["root_id"] if iteration else request.update_id,
        parent_id=iteration["parent_id"] if iteration else None,
        phase=state.phase.value, attempt=state.attempt, state_revision=state.revision,
        created_at=request.created_at, updated_at=state.updated_at,
        candidate_revision=request.candidate_sha, changed_paths=list(request.changed_paths),
        target_app=request.app_path, last_verified_runtime=runtime,
        rollback_available=_rollback_available(record, runtime),
        verifier_verdict=None if verifier is None else verifier["verdict"], verifier=verifier,
        diagnosis=stage(diagnostic), source_repair_result=repair_summary, iteration=iteration,
    )
    result["snapshot_id"] = _digest(result)
    return _bounded(result)


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        raise ConcurrentUpdateError("self-update projection exceeds output limit")
    return value


def _exists(store):
    if not store.root.exists() and not store.root.is_symlink():
        return False
    store._private_directory(store.root)
    return True


def read_status(store: SelfUpdateStore, *, session_id: str, update_id: str | None = None) -> dict:
    _session(session_id)
    if update_id is not None:
        _validate_update_id(update_id)
    if not _exists(store):
        raise UpdateNotFoundError("no self-update exists")
    with store._locked(read_only=True):
        record = (store._load_active_unlocked(read_only=True) if update_id is None
                  else store._load_unlocked(update_id, read_only=True))
        if record is None:
            raise UpdateNotFoundError("no active self-update")
        if record.request.session_id != session_id:
            raise ProjectionAccessError("self-update belongs to another origin session")
        if not is_terminal(record.state.phase):
            active = store._load_active_unlocked(read_only=True)
            if active is None or active.request.update_id != record.request.update_id:
                raise CorruptUpdateStateError("non-terminal update does not own the active slot")
        return _project(store, record)


def _records(store):
    # ponytail: bounded directory scan; use an existing store index if update
    # history grows beyond this limit, rather than an unbounded polling scan.
    records = []
    for index, path in enumerate(store.root.iterdir()):
        if index >= READ_SCAN_LIMIT:
            raise ConcurrentUpdateError("self-update history exceeds scan limit")
        if path.name.startswith("su_"):
            records.append(store._load_unlocked(path.name, read_only=True))
    nonterminal = [r.request.update_id for r in records if not is_terminal(r.state.phase)]
    if nonterminal:
        active = store._load_active_unlocked(read_only=True)
        if active is None or nonterminal != [active.request.update_id]:
            raise CorruptUpdateStateError("inconsistent active update records")
    return records


def list_status(store: SelfUpdateStore, *, session_id: str, limit: int = 20, cursor: str | None = None):
    _session(session_id)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("invalid self-update page size")
    upper = after = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (set(value) != {"schema", "session_id", "upper", "after"}
                    or type(value["schema"]) is not int or value["schema"] != 1 or value["session_id"] != session_id):
                raise ValueError
            upper, after = value["upper"], value["after"]
            for item in (upper, after):
                if (not isinstance(item, list) or len(item) != 2 or type(item[0]) not in (int, float)
                        or not math.isfinite(item[0]) or item[0] < 0):
                    raise ValueError
                _validate_update_id(item[1])
            if after > upper:
                raise ValueError
        except Exception as exc:
            raise ValueError("invalid self-update cursor") from exc
    if not _exists(store):
        return {"items": [], "next_cursor": None}
    with store._locked(read_only=True):
        records = [r for r in _records(store) if r.request.session_id == session_id]
        def key(r):
            return [r.request.created_at, r.request.update_id]
        records.sort(key=key, reverse=True)
        if upper is None and records:
            upper = key(records[0])
        records = [r for r in records if key(r) <= upper and (after is None or key(r) < after)]
        page = records[:limit]
        next_cursor = None
        if len(records) > limit:
            value = dict(schema=1, session_id=session_id, upper=upper, after=key(page[-1]))
            next_cursor = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()
        return _bounded({"items": [_project(store, r) for r in page], "next_cursor": next_cursor})


def running_status(store: SelfUpdateStore):
    if not _exists(store):
        return []
    with store._locked(read_only=True):
        pointers = []
        for name in ("diagnosis-pending.json", "source-repair-pending.json", "iteration-pending.json"):
            value = _optional(store.root / name)
            if value is not None:
                if set(value) != {"schema", "update_id"} or type(value["schema"]) is not int or value["schema"] != 1:
                    raise CorruptUpdateStateError("invalid pending update pointer")
                pointers.append(_validate_update_id(value["update_id"]))
        records = _records(store)
        return _bounded([_project(store, r) for r in records
                         if not is_terminal(r.state.phase) or r.request.update_id in pointers])
