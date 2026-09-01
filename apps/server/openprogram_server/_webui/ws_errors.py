"""Low-sensitivity WebSocket command error envelopes."""

from __future__ import annotations

from typing import Any


_MAX_METADATA_CHARS = 128
_MESSAGES = {
    "handler_error": "Action failed",
    "unknown_action": "Unknown action",
}


def _safe_metadata(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_METADATA_CHARS:
        return None
    if not value.isprintable():
        return None
    return value


def operation_error_frame(
    command: object,
    *,
    code: str,
    retryable: bool = False,
) -> dict[str, object]:
    """Build an error frame without reflecting payloads or exception text."""
    cmd = command if isinstance(command, dict) else {}
    action = _safe_metadata(cmd.get("action"))
    session_id = _safe_metadata(cmd.get("session_id"))
    return {
        "type": "operation_error",
        "data": {
            "request_id": _safe_metadata(cmd.get("request_id")),
            "code": code,
            "message": _MESSAGES.get(code, "Operation failed"),
            "session_id": session_id,
            "retryable": bool(retryable),
            "action": action,
        },
    }
