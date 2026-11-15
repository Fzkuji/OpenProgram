"""Durable, bounded records for user-visible runtime failures."""

from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from openprogram.state.store.record_log import RecordLog, record_log


MAX_RECORDS_PER_PRINCIPAL = 1_000
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 96
_CURSOR_PREFIX = "v1"
_SCOPES = frozenset({"session", "job", "settings", "channel", "agent", "transport", "system"})
_SEVERITIES = frozenset({"info", "warning", "error", "fatal"})
_CLOSE_REASONS = frozenset({None, "acknowledged", "recovered", "entity_deleted"})
_ERROR_ID_PATTERN = re.compile(r"err_[0-9a-f]{32}")


def _encode_cursor(occurred_at_epoch: float, error_id: str) -> str:
    return f"{_CURSOR_PREFIX}:{occurred_at_epoch.hex()}:{error_id}"


def _decode_cursor(cursor: object) -> tuple[float, str]:
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > MAX_CURSOR_LENGTH
        or not cursor.isascii()
    ):
        raise ValueError("user error cursor is invalid")
    try:
        prefix, encoded_epoch, error_id = cursor.split(":", 2)
        occurred_at_epoch = float.fromhex(encoded_epoch)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("user error cursor is invalid") from exc
    if (
        prefix != _CURSOR_PREFIX
        or not math.isfinite(occurred_at_epoch)
        or _ERROR_ID_PATTERN.fullmatch(error_id) is None
        or cursor != _encode_cursor(occurred_at_epoch, error_id)
    ):
        raise ValueError("user error cursor is invalid")
    return occurred_at_epoch, error_id


def is_user_error_cursor(value: object) -> bool:
    try:
        _decode_cursor(value)
    except (OverflowError, ValueError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class UserErrorRecord:
    principal_id: str
    error_id: str
    request_id: str | None
    scope: str
    code: str
    message: str
    action: str | None
    session_id: str | None
    operation_id: str | None
    retryable: bool
    severity: str
    correlation_id: str
    occurred_at: str
    occurred_at_epoch: float
    closed_at: str | None = None
    close_reason: str | None = None

    def wire_data(self) -> dict[str, object]:
        """Return only the canonical low-sensitivity wire fields."""
        return {
            "error_id": self.error_id,
            "request_id": self.request_id,
            "scope": self.scope,
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "session_id": self.session_id,
            "operation_id": self.operation_id,
            "retryable": self.retryable,
            "severity": self.severity,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, slots=True)
class UserErrorPage:
    records: tuple[UserErrorRecord, ...]
    next_cursor: str | None


class UserErrorStore:
    """Error ledger scoped to one active OpenProgram profile."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._resolved_path: Path | None = None

    @property
    def path(self) -> Path:
        if self._resolved_path is None:
            if self._explicit_path is None:
                from openprogram.state.paths import (
                    ensure_state_dir,
                    get_user_errors_path,
                )

                ensure_state_dir()
                path = get_user_errors_path()
            else:
                path = self._explicit_path
                path.parent.mkdir(parents=True, exist_ok=True)
            self._resolved_path = path
        return self._resolved_path

    @property
    def _log(self) -> RecordLog:
        return record_log(self.path)

    @staticmethod
    def _prune(log: RecordLog, principal_id: str, now: float) -> None:
        cutoff = now - RETENTION_SECONDS
        removed = [key for key, row in log.records.items() if row["occurred_at_epoch"] < cutoff]
        expired = set(removed)
        owned = sorted(
            (
                row for key, row in log.records.items()
                if row["principal_id"] == principal_id and key not in expired
            ),
            key=_position,
            reverse=True,
        )
        removed.extend(row["error_id"] for row in owned[MAX_RECORDS_PER_PRINCIPAL:])
        log.delete(removed)

    def record(self, record: UserErrorRecord, *, now: float | None = None) -> None:
        _require_valid(record)
        prune_now = time.time() if now is None else float(now)
        with self._log.locked() as log:
            if record.error_id in log.records:
                raise ValueError("user error id already exists")
            log.put(record.error_id, asdict(record))
            self._prune(log, record.principal_id, prune_now)

    def get(
        self,
        principal_id: str,
        error_id: str,
        *,
        now: float | None = None,
    ) -> UserErrorRecord | None:
        prune_now = time.time() if now is None else float(now)
        with self._log.locked() as log:
            self._prune(log, principal_id, prune_now)
            row = log.records.get(error_id)
        return UserErrorRecord(**row) if row and row["principal_id"] == principal_id else None

    def list_open(
        self,
        principal_id: str,
        *,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
        now: float | None = None,
    ) -> UserErrorPage:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("user error page limit must be an integer")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(
                f"user error page limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        before = None if cursor is None else _decode_cursor(cursor)
        prune_now = time.time() if now is None else float(now)
        with self._log.locked() as log:
            self._prune(log, principal_id, prune_now)
            rows = sorted(
                (
                    row for row in log.records.values()
                    if row["principal_id"] == principal_id
                    and row["closed_at"] is None
                    and (before is None or _position(row) < before)
                ),
                key=_position,
                reverse=True,
            )
        visible = rows[:limit]
        next_cursor = (
            _encode_cursor(float(visible[-1]["occurred_at_epoch"]), str(visible[-1]["error_id"]))
            if len(rows) > limit
            else None
        )
        return UserErrorPage(
            records=tuple(UserErrorRecord(**row) for row in visible),
            next_cursor=next_cursor,
        )

    def acknowledge(
        self,
        principal_id: str,
        error_id: str,
        occurred_at: str,
        now: float | None = None,
    ) -> UserErrorRecord | None:
        """Idempotently close one exact record owned by ``principal_id``."""
        prune_now = time.time() if now is None else float(now)
        with self._log.locked() as log:
            self._prune(log, principal_id, prune_now)
            row = log.records.get(error_id)
            if row is None or row["principal_id"] != principal_id:
                return None
            if row["closed_at"] is None:
                row = {**row, "closed_at": occurred_at, "close_reason": "acknowledged"}
                log.put(error_id, row)
        return UserErrorRecord(**row)


def _require_valid(record: UserErrorRecord) -> None:
    if record.scope not in _SCOPES:
        raise ValueError(f"unknown user error scope: {record.scope}")
    if record.severity not in _SEVERITIES:
        raise ValueError(f"unknown user error severity: {record.severity}")
    if record.close_reason not in _CLOSE_REASONS:
        raise ValueError(f"unknown user error close reason: {record.close_reason}")
    if (record.closed_at is None) != (record.close_reason is None):
        raise ValueError("user error close time and reason must be set together")


def _position(row: dict) -> tuple[float, str]:
    return (row["occurred_at_epoch"], row["error_id"])
