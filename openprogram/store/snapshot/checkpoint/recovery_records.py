"""Structural admission for persisted history and rewind records, without executing them."""
from __future__ import annotations

import json
from pathlib import Path
import re

_STATUSES = {"prepared", "applying", "committed", "rolled_back", "recovery_required", "aborted"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _filename(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and "\0" not in value
            and value not in {".", ".."} and Path(value).name == value)


def _parent_chain(value: object, path: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    root = value.get("root")
    if not isinstance(root, str) or "\0" in root or not Path(root).is_absolute():
        return False
    if any(type(value.get(key)) is not int for key in ("root_dev", "root_ino")):
        return False
    components = value.get("components")
    if not isinstance(components, list):
        return False
    names = []
    for component in components:
        if not isinstance(component, dict):
            return False
        name = component.get("name")
        # Parent references may occur in an absolute input path. They must
        # match that path exactly; separators cannot add unrecorded components.
        if not isinstance(name, str) or not name or "\0" in name or Path(name).name != name:
            return False
        if any(type(component.get(key)) is not int for key in ("dev", "ino")):
            return False
        names.append(name)
    return Path(root, *names) == Path(path).parent


def _state(value: object, *, executable: bool = False, path: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    if path is not None and not _parent_chain(value.get("parent_chain"), path):
        return False
    if value.get("kind") == "absent":
        return True
    digest = value.get("digest")
    if (value.get("kind") != "regular" or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None):
        return False
    if executable:
        mode = value.get("mode")
        if mode is not None and mode != "":
            if not isinstance(mode, str) or re.fullmatch(r"[0-7]{1,4}", mode) is None:
                return False
        blob_path = value.get("blob_path")
        if blob_path is not None and blob_path != "":
            if (not isinstance(blob_path, str) or "\0" in blob_path
                    or not Path(blob_path).is_absolute() or not Path(blob_path).name):
                return False
        blob_ref = value.get("blob_ref")
        if blob_ref is not None and blob_ref != "" and not _filename(blob_ref):
            return False
    return True


def valid_rewind_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        return False
    for name in ("expected_head_id", "target_head_id"):
        if name not in value or (value[name] is not None and not isinstance(value[name], str)):
            return False
    transaction_id = value.get("transaction_id")
    if transaction_id is not None and transaction_id != "" and not _filename(transaction_id):
        return False
    actions = value.get("actions")
    if not isinstance(actions, list):
        return False
    for action in actions:
        if not isinstance(action, dict):
            return False
        path = action.get("path")
        if not isinstance(path, str) or "\0" in path or not Path(path).is_absolute() or not Path(path).name:
            return False
        if not _state(action.get("target"), path=path) or not _state(action.get("rollback"), executable=True):
            return False
    return True


def read_rewind_record(path: Path) -> dict | None:
    """Return no record for malformed data; leave I/O errors and cancellation visible."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return value if valid_rewind_record(value) else None


def invalid_rewind_result(path: Path) -> dict:
    return {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
            "error": "invalid rewind intent", "intent_path": str(path),
            "restored_paths": []}


def read_history_record(path: Path) -> dict | None:
    """Admit fields consumed by non-executing history receipt paths."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        return None
    actions = value.get("actions")
    if not isinstance(actions, list):
        return None
    for action in actions:
        if not isinstance(action, dict):
            return None
        target = action.get("path")
        if (not isinstance(target, str) or "\0" in target
                or not Path(target).is_absolute() or not Path(target).name):
            return None
    return value


def invalid_history_result(path: Path) -> dict:
    return {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
            "error": "invalid history intent", "intent_path": str(path),
            "restored_paths": []}
