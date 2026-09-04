"""Controller-owned Desktop recovery intent; never update/verifier authority."""
from __future__ import annotations

import math
import time

from .projection import ProjectionAccessError, _exists, _scoped_record
from .rollback_intent import RECOVERY_SECONDS, load_rollback_intent
from .types import UpdateNotFoundError, UpdatePhase, _validate_update_id
from .verification_channel import _digest, _read
from .verifier_config import load_verifier_config

REOPEN_PROTOCOL = 1


class ReopenUnavailable(ValueError):
    """A finite reason for normal startup fallback, not an update verdict."""


def _intent_path(store, record):
    return store.root / record.request.update_id / f"reopen-{record.state.attempt}.json"


def _load_intent(store, record):
    request = record.request
    owner = load_verifier_config(store, record)["authority"]["principal_id"]
    try:
        value = _read(_intent_path(store, record))
    except FileNotFoundError:
        raise ReopenUnavailable("intent_missing") from None
    if set(value) != {
        "schema", "update_id", "candidate_sha", "request_sha256", "attempt",
        "session_id", "owner_principal_id", "created_at", "expires_at", "startup_action",
    } or (
        type(value["schema"]) is not int or value["schema"] != REOPEN_PROTOCOL
        or value["update_id"] != request.update_id
        or value["candidate_sha"] != request.candidate_sha
        or value["request_sha256"] != _digest(request.to_dict())
        or type(value["attempt"]) is not int or value["attempt"] != record.state.attempt
        or value["session_id"] != request.session_id
        or value["owner_principal_id"] != owner
        or value["startup_action"] != "restore_if_open"
        or any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
               for k in ("created_at", "expires_at"))
        or not request.created_at <= value["created_at"] <= min(
            time.time(), request.created_at + request.timeout_seconds)
        or value["expires_at"] != request.created_at + request.timeout_seconds + RECOVERY_SECONDS
    ):
        raise ReopenUnavailable("intent_invalid")
    if time.time() >= value["expires_at"]:
        raise ReopenUnavailable("intent_expired")
    return value


def prepare_reopen(store, update_id: str) -> dict:
    """Persist before activation; re-entry cannot extend its frozen deadline."""
    with store._locked():
        record = store._load_unlocked(update_id, read_only=True)
        if record.state.phase is not UpdatePhase.READY:
            raise ReopenUnavailable("activation_not_ready")
        path = _intent_path(store, record)
        if path.exists() or path.is_symlink():
            return _load_intent(store, record)
        request = record.request
        now = time.time()
        if now >= request.created_at + request.timeout_seconds:
            raise ReopenUnavailable("intent_expired")
        owner = load_verifier_config(store, record)["authority"]["principal_id"]
        value = dict(schema=REOPEN_PROTOCOL, update_id=update_id,
                     candidate_sha=request.candidate_sha, request_sha256=_digest(request.to_dict()),
                     attempt=record.state.attempt, session_id=request.session_id,
                     owner_principal_id=owner, created_at=now,
                     expires_at=request.created_at + request.timeout_seconds + RECOVERY_SECONDS,
                     startup_action="restore_if_open")
        store._write_json(path, value)
        return value


def _resolve(store, update_id, principal_id):
    from openprogram.store import default_store

    record = store._load_unlocked(update_id, read_only=True)
    intent = _load_intent(store, record)
    if principal_id != intent["owner_principal_id"]:
        raise ProjectionAccessError("reopen owner differs from update owner")
    _scoped_record(store, intent["session_id"], update_id)
    if record.state.phase not in {
        UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING, UpdatePhase.SUCCEEDED,
        UpdatePhase.ROLLED_BACK, UpdatePhase.NEEDS_MANUAL_RECOVERY,
    }:
        raise ReopenUnavailable("activation_not_started")
    # _open checks on-disk existence even for a cached session. Do not recreate
    # deleted sessions or advance history while resolving a launch argument.
    db = default_store()
    with db._session_lock(record.request.session_id):
        pair = db._open(record.request.session_id)
        if pair is None:
            raise ReopenUnavailable("session_missing")
        origin = pair[1].nodes_by_id.get(record.request.origin_assistant_id)
        if origin is None or origin.role != "llm":
            raise ReopenUnavailable("origin_missing")
    rollback = load_rollback_intent(store, record)
    kind = "rollback" if rollback is not None else "activation"
    return dict(schema=REOPEN_PROTOCOL, update_id=update_id, attempt=record.state.attempt,
                session_id=intent["session_id"], launch_kind=kind,
                reopen_id=_digest({"intent": intent, "rollback": rollback}),
                expires_at=intent["expires_at"])


def _receipt_path(store, value):
    return store.root / value["update_id"] / f"reopen-ack-{value['attempt']}-{value['launch_kind']}.json"


def _receipt(store, value):
    try:
        receipt = _read(_receipt_path(store, value))
    except FileNotFoundError:
        return None
    if set(receipt) != {*value, "loaded_at"} or _digest(
        {k: receipt[k] for k in value}
    ) != _digest(value) or (
        type(receipt["loaded_at"]) not in (int, float)
        or not math.isfinite(receipt["loaded_at"])
        or not 0 <= receipt["loaded_at"] <= min(time.time(), value["expires_at"])
    ):
        raise ReopenUnavailable("ack_invalid")
    return receipt


def resolve_reopen(store, *, update_id: str, principal_id: str) -> dict:
    _validate_update_id(update_id)
    if not _exists(store):
        raise UpdateNotFoundError("no self-update exists")
    with store._locked(read_only=True):
        value = _resolve(store, update_id, principal_id)
        return {**value, "status": "acknowledged" if _receipt(store, value) else "pending"}


def acknowledge_reopen(store, *, update_id: str, principal_id: str,
                       session_id: str, reopen_id: str) -> dict:
    """Save a location ACK, not evidence of installed behavior or verification."""
    _validate_update_id(update_id)
    if not _exists(store):
        raise UpdateNotFoundError("no self-update exists")
    # The read-only lock uses an exclusive flock without repairing file modes.
    # Only the explicit ACK receipt is written, atomically, under that same lock.
    with store._locked(read_only=True):
        value = _resolve(store, update_id, principal_id)
        if session_id != value["session_id"] or reopen_id != value["reopen_id"]:
            raise ReopenUnavailable("ack_identity_mismatch")
        if _receipt(store, value) is None:
            store._write_json(_receipt_path(store, value), {**value, "loaded_at": time.time()})
        return {**value, "status": "acknowledged"}
