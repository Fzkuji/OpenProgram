"""Prepare, publish and read document operations under a recovery directory."""
from __future__ import annotations

import json
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from openprogram._compat import is_link_metadata
from . import capture, file_state, manifest, transactions
from .capture import MutationJournalError


def _manual_operation_path(recovery_root: Path | None, operation_id: str) -> Path:
    if recovery_root is None:
        raise TypeError("recovery_root is required for manual document operations")
    if (
        not isinstance(operation_id, str)
        or len(operation_id) != 32
        or any(char not in "0123456789abcdef" for char in operation_id)
    ):
        raise ValueError("invalid operation_id")
    return recovery_root / "operations" / operation_id



def _manual_descriptor(state: dict) -> dict:
    if state.get("kind") == "absent":
        return {"kind": "absent", "sha256": None, "mode": None, "size": 0, "blob_ref": None}
    return {
        "kind": "regular", "blob_ref": state["blob_ref"],
        "sha256": state["sha256"], "mode": state["mode"], "size": state["size"],
    }



@contextmanager
def _manual_operation_lock(recovery_root: Path | None, operation_id: str):
    from openprogram import _compat as fcntl

    operation_dir = _manual_operation_path(recovery_root, operation_id)
    operation_dir.mkdir(parents=True, exist_ok=True)
    with (operation_dir / ".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)



def publish_document(
    recovery_root: Path | None, operation_id: str, target_path: str | Path, source_path: str | Path,
    *, expected_revision: str | None = None, expected_mtime: int | float | None = None,
    fingerprint: str, metadata: dict | None = None,
) -> dict:
    with _manual_operation_lock(recovery_root, operation_id):
        return _publish_document_locked(recovery_root, 
            operation_id, target_path, source_path,
            expected_revision=expected_revision, expected_mtime=expected_mtime,
            fingerprint=fingerprint, metadata=metadata,
        )



def _publish_document_locked(
    recovery_root: Path | None, operation_id: str, target_path: str | Path, source_path: str | Path,
    *, expected_revision: str | None = None, expected_mtime: int | float | None = None,
    fingerprint: str, metadata: dict | None = None,
) -> dict:
    """Durably publish one bounded ordinary file and return its receipt."""
    operation_dir = _manual_operation_path(recovery_root, operation_id)
    intent_path = operation_dir / "intent.json"
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("fingerprint is required")
    if intent_path.exists():
        existing = read_document_operation(recovery_root, operation_id)
        if existing.get("fingerprint") != fingerprint:
            raise ValueError("operation fingerprint conflict")
        if existing.get("status") != "prepared":
            return existing
        return existing
    target = Path(target_path)
    source = Path(source_path)
    if not target.is_absolute() or not source.is_absolute():
        raise ValueError("document paths must be absolute")
    operation_dir.mkdir(parents=True, exist_ok=True)
    before = {"kind": "absent"}
    try:
        target_info = os.lstat(target)
    except FileNotFoundError:
        target_info = None
    if target_info is not None:
        if (not stat.S_ISREG(target_info.st_mode) or is_link_metadata(target_info)
                or target_info.st_nlink != 1 or target_info.st_size > 64 * 1024 * 1024):
            raise MutationJournalError("document target must be an ordinary file of at most 64 MiB")
        before = capture._capture_manual_blob(target, operation_dir / "before")
    candidate = capture._capture_manual_blob(source, operation_dir / "candidate")
    # A publication changes bytes while retaining the target's existing
    # permissions.  Source permissions are relevant only when creating a
    # previously absent target.
    if before.get("kind") == "regular":
        candidate["mode"] = before["mode"]
    current_revision = before.get("sha256") if before["kind"] == "regular" else "absent"
    if expected_revision is not None and expected_revision != current_revision:
        raise MutationJournalError("document baseline does not match")
    if expected_mtime is not None and target_info is not None and target_info.st_mtime != expected_mtime:
        raise MutationJournalError("document mtime does not match")
    parent_chain = file_state._capture_parent_chain(str(target))
    if before.get("kind") == "regular":
        before["blob_path"] = str(operation_dir / before["blob_ref"])
    before_state = {**before, "parent_chain": parent_chain}
    target_state = {**candidate, "parent_chain": parent_chain, "blob_path": str(operation_dir / candidate["blob_ref"])}
    if before["kind"] == "absent":
        before_state["parent_chain"] = parent_chain
    intent = {
        "version": 1, "status": "prepared", "operation_id": operation_id,
        "transaction_id": f"document_{uuid.uuid4().hex}", "fingerprint": fingerprint,
        "metadata": metadata or {}, "expected_revision": expected_revision,
        "expected_mtime": expected_mtime, "target_path": str(target),
        "actions": [{"path": str(target), "expected_current": before_state,
                      "target": target_state, "rollback": before_state, "state": "pending", "error": None}],
        "before": _manual_descriptor(before), "after": _manual_descriptor(candidate),
    }
    manifest.save(intent_path, intent)
    result = transactions._execute_history_intent(intent, intent_path, operation_dir)
    if result.get("status") == "committed":
        intent["mtime"] = target.stat().st_mtime if target.exists() else None
        manifest.save(intent_path, intent)
    result.update({"fingerprint": fingerprint, "before": intent["before"], "after": intent["after"],
                   "revision": candidate["sha256"], "mtime": intent.get("mtime")})
    return result



def read_document_operation(recovery_root: Path | None, operation_id: str) -> dict:
    path = _manual_operation_path(recovery_root, operation_id) / "intent.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, FileNotFoundError):
            return {"status": "not_found", "operation_id": operation_id}
        return {"status": "recovery_required", "operation_id": operation_id,
                "error_code": "RECOVERY_REQUIRED", "error": "invalid document intent"}
    if not isinstance(value, dict):
        return {"status": "recovery_required", "operation_id": operation_id,
                "error_code": "RECOVERY_REQUIRED", "error": "invalid document intent"}
    def valid_descriptor(descriptor: object) -> bool:
        if not isinstance(descriptor, dict):
            return False
        kind = descriptor.get("kind")
        if not isinstance(kind, str) or kind not in {"absent", "regular"}:
            return False
        if descriptor["kind"] == "absent":
            return descriptor.get("size") == 0
        return (
            isinstance(descriptor.get("blob_ref"), str)
            and bool(descriptor["blob_ref"])
            and descriptor["blob_ref"] not in {".", ".."}
            and Path(descriptor["blob_ref"]).name == descriptor["blob_ref"]
            and isinstance(descriptor.get("sha256"), str)
            and len(descriptor["sha256"]) == 64
            and all(char in "0123456789abcdef" for char in descriptor["sha256"])
            and isinstance(descriptor.get("mode"), str)
            and isinstance(descriptor.get("size"), int)
            and descriptor["size"] >= 0
        )
    status = value.get("status")
    invalid = (
        not isinstance(status, str)
        or status not in {"prepared", "applying", "committed", "rolled_back", "aborted", "recovery_required"}
        or value.get("operation_id") != operation_id
        or not valid_descriptor(value.get("before"))
        or not valid_descriptor(value.get("after"))
    )
    if invalid or status in {"prepared", "applying"}:
        value = {**value, "status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
                 "error": "invalid or incomplete document operation requires recovery"}
    after = value.get("after")
    revision = after.get("sha256") if isinstance(after, dict) else None
    return {"status": value.get("status", "error"), "transaction_id": value.get("transaction_id"),
            "operation_id": value.get("operation_id", operation_id), "fingerprint": value.get("fingerprint"),
            "before": value.get("before"), "after": value.get("after"),
            "revision": revision, "mtime": value.get("mtime"),
            "error_code": value.get("error_code"), "error": value.get("error")}

