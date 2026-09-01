"""Low-sensitivity, durable WebSocket command error envelopes."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any

from openprogram.webui.user_errors import UserErrorRecord, UserErrorStore


_MAX_METADATA_CHARS = 128
_MAX_MESSAGE_CHARS = 256
_SCOPES = frozenset({
    "session", "job", "settings", "channel", "agent", "transport", "system",
})
_SEVERITIES = frozenset({"info", "warning", "error", "fatal"})
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ERROR_ID_RE = re.compile(r"^err_[0-9a-f]{32}$")
_CORRELATION_ID_RE = re.compile(r"^corr_[0-9a-f]{32}$")
_WIRE_FIELDS = frozenset({
    "error_id",
    "request_id",
    "scope",
    "code",
    "message",
    "action",
    "session_id",
    "operation_id",
    "retryable",
    "severity",
    "correlation_id",
    "occurred_at",
})
_MESSAGES = {
    "handler_error": "Action failed",
    "invalid_request": "Invalid request",
    "unknown_action": "Unknown action",
}

_ACTION_SCOPES = {
    "list_agents": "agent",
    "add_agent": "agent",
    "delete_agent": "agent",
    "set_default_agent": "agent",
    "get_settings": "settings",
    "set_setting": "settings",
    "list_channel_accounts": "channel",
    "add_channel_account": "channel",
    "remove_channel_account": "channel",
    "list_channel_bindings": "channel",
    "add_binding": "channel",
    "remove_binding": "channel",
    "list_session_aliases": "channel",
    "attach_session": "channel",
    "detach_session": "channel",
}


class OperationError(RuntimeError):
    """Safe command failure metadata consumed by the public dispatcher."""

    def __init__(
        self,
        code: str,
        *,
        scope: str,
        retryable: bool = False,
        severity: str = "error",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.scope = scope
        self.retryable = retryable
        self.severity = severity


def safe_operation_metadata(value: Any) -> str | None:
    """Accept only short printable identifiers from an untrusted command."""
    if not isinstance(value, str) or not value or len(value) > _MAX_METADATA_CHARS:
        return None
    if not value.isprintable():
        return None
    return value


def _scope_for(action: str | None, session_id: str | None) -> str:
    if action in _ACTION_SCOPES:
        return _ACTION_SCOPES[action]
    if session_id is not None:
        return "session"
    return "system"


def utc_timestamp(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def principal_id_for_websocket(ws: object) -> str:
    """Read the authenticated owner from ASGI scope, never from a command."""
    scope = getattr(ws, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    authority = state.get("authority") if isinstance(state, dict) else None
    from openprogram.agent.authority import (
        AuthorityError,
        normalize_authority,
        owner_authority,
    )

    normalized = normalize_authority(authority)
    principal_id = normalized.get("principal_id")
    if normalized.get("authority_tier") != "owner" or not isinstance(
        principal_id,
        str,
    ):
        raise PermissionError("authenticated owner authority is required")
    try:
        canonical = owner_authority(principal_id)
    except AuthorityError as exc:
        raise PermissionError("authenticated owner authority is invalid") from exc
    if normalized != canonical:
        raise PermissionError("authenticated owner authority is invalid")
    return principal_id


def default_user_error_store() -> UserErrorStore:
    """Return a fresh profile-aware handle; never cache a profile path."""
    return UserErrorStore()


def operation_error_frame(
    command: object,
    *,
    code: str,
    retryable: bool = False,
    scope: str | None = None,
    severity: str = "error",
    occurred_at_epoch: float | None = None,
) -> dict[str, object]:
    """Build an error frame without reflecting payloads or exception text."""
    if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
        raise ValueError("operation error code is invalid")
    if not isinstance(retryable, bool):
        raise TypeError("operation error retryable must be boolean")
    cmd = command if isinstance(command, dict) else {}
    action = safe_operation_metadata(cmd.get("action"))
    session_id = safe_operation_metadata(cmd.get("session_id"))
    resolved_scope = _scope_for(action, session_id) if scope is None else scope
    if resolved_scope not in _SCOPES:
        raise ValueError("operation error scope is invalid")
    if severity not in _SEVERITIES:
        raise ValueError("operation error severity is invalid")
    epoch = time.time() if occurred_at_epoch is None else occurred_at_epoch
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        raise TypeError("operation error timestamp must be numeric")
    if not math.isfinite(epoch):
        raise ValueError("operation error timestamp must be finite")
    return {
        "type": "operation_error",
        "data": {
            "error_id": f"err_{uuid.uuid4().hex}",
            "request_id": safe_operation_metadata(cmd.get("request_id")),
            "scope": resolved_scope,
            "code": code,
            "message": _MESSAGES.get(code, "Operation failed"),
            "action": action,
            "session_id": session_id,
            "operation_id": safe_operation_metadata(cmd.get("operation_id")),
            "retryable": bool(retryable),
            "severity": severity,
            "correlation_id": f"corr_{uuid.uuid4().hex}",
            "occurred_at": utc_timestamp(epoch),
        },
    }


def is_error_id(value: object) -> bool:
    return isinstance(value, str) and _ERROR_ID_RE.fullmatch(value) is not None


def operation_recovered_frame(
    error_id: str,
    *,
    scope: str,
    operation_id: str | None,
    occurred_at_epoch: float | None = None,
) -> dict[str, object]:
    """Build one exact-ID closure notification for every connected tab."""
    if not is_error_id(error_id):
        raise ValueError("operation recovery error ID is invalid")
    if scope not in _SCOPES:
        raise ValueError("operation recovery scope is invalid")
    if operation_id is not None and safe_operation_metadata(operation_id) != operation_id:
        raise ValueError("operation recovery operation ID is invalid")
    epoch = time.time() if occurred_at_epoch is None else occurred_at_epoch
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        raise TypeError("operation recovery timestamp must be numeric")
    if not math.isfinite(epoch):
        raise ValueError("operation recovery timestamp must be finite")
    return {
        "type": "operation_recovered",
        "data": {
            "error_ids": [error_id],
            "scope": scope,
            "operation_id": operation_id,
            "occurred_at": utc_timestamp(epoch),
        },
    }


def _required_text(
    data: dict[str, object],
    key: str,
    *,
    pattern: re.Pattern[str] | None = None,
    max_chars: int = _MAX_METADATA_CHARS,
) -> str:
    value = data.get(key)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_chars
        or not value.isprintable()
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise ValueError(f"operation error {key} is invalid")
    return value


def _optional_metadata(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if safe_operation_metadata(value) != value:
        raise ValueError(f"operation error {key} is invalid")
    return value


def persist_operation_error_frame(
    ws: object,
    frame: dict[str, object],
    *,
    store: UserErrorStore | None = None,
) -> None:
    """Commit one generated frame before it can enter a delivery queue."""
    if frame.get("type") != "operation_error":
        raise ValueError("operation_error frame type is required")
    data = frame.get("data")
    if not isinstance(data, dict):
        raise ValueError("operation_error data is required")
    if set(data) != _WIRE_FIELDS:
        raise ValueError("operation_error fields are invalid")
    error_id = _required_text(data, "error_id", pattern=_ERROR_ID_RE)
    request_id = _optional_metadata(data, "request_id")
    scope = _required_text(data, "scope")
    code = _required_text(data, "code", pattern=_CODE_RE)
    message = _required_text(data, "message", max_chars=_MAX_MESSAGE_CHARS)
    action = _optional_metadata(data, "action")
    session_id = _optional_metadata(data, "session_id")
    operation_id = _optional_metadata(data, "operation_id")
    retryable = data.get("retryable")
    severity = _required_text(data, "severity")
    correlation_id = _required_text(
        data,
        "correlation_id",
        pattern=_CORRELATION_ID_RE,
    )
    occurred_at = _required_text(data, "occurred_at")
    if scope not in _SCOPES or severity not in _SEVERITIES:
        raise ValueError("operation_error enum field is invalid")
    if not isinstance(retryable, bool):
        raise ValueError("operation_error retryable is invalid")
    if not occurred_at.endswith("Z"):
        raise ValueError("operation_error occurred_at must be UTC")
    try:
        parsed_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("operation_error occurred_at is invalid") from exc
    if parsed_at.utcoffset() != timedelta(0):
        raise ValueError("operation_error occurred_at must be UTC")
    epoch = parsed_at.timestamp()
    if not math.isfinite(epoch):
        raise ValueError("operation_error occurred_at must be finite")
    record = UserErrorRecord(
        principal_id=principal_id_for_websocket(ws),
        error_id=error_id,
        request_id=request_id,
        scope=scope,
        code=code,
        message=message,
        action=action,
        session_id=session_id,
        operation_id=operation_id,
        retryable=retryable,
        severity=severity,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        occurred_at_epoch=epoch,
    )
    (store or default_user_error_store()).record(record, now=epoch)
