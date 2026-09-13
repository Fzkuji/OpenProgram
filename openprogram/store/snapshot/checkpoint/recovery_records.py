"""Structural admission for persisted rewind records, without executing them."""
from __future__ import annotations

import json
from pathlib import Path
import re

_STATUSES = {"prepared", "applying", "committed", "rolled_back", "recovery_required", "aborted"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _state(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("kind") == "absent":
        return True
    digest = value.get("digest")
    return (value.get("kind") == "regular" and isinstance(digest, str)
            and _DIGEST.fullmatch(digest) is not None)


def valid_rewind_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        return False
    for name in ("expected_head_id", "target_head_id"):
        if name not in value or (value[name] is not None and not isinstance(value[name], str)):
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
        if not _state(action.get("target")) or not _state(action.get("rollback")):
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
