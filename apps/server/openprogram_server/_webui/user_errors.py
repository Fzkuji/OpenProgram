"""Durable, bounded records for user-visible runtime failures."""

from __future__ import annotations

import os
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


MAX_RECORDS_PER_PRINCIPAL = 1_000
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 96
_INITIALIZE_TIMEOUT_SECONDS = 5.0
_CURSOR_PREFIX = "v1"
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
    """SQLite error ledger scoped to one active OpenProgram profile."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._resolved_path: Path | None = None
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def path(self) -> Path:
        if self._resolved_path is None:
            if self._explicit_path is None:
                from openprogram.paths import (
                    ensure_state_dir,
                    get_user_errors_db_path,
                )

                ensure_state_dir()
                path = get_user_errors_db_path()
            else:
                path = self._explicit_path
                path.parent.mkdir(parents=True, exist_ok=True)
            self._resolved_path = path
        return self._resolved_path

    def _connect(self) -> sqlite3.Connection:
        path = self.path
        existed = path.exists()
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        if not existed:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            current_mode = str(
                conn.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            if current_mode != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_errors (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_id TEXT NOT NULL UNIQUE,
                    principal_id TEXT NOT NULL,
                    request_id TEXT,
                    scope TEXT NOT NULL CHECK (
                        scope IN (
                            'session', 'job', 'settings', 'channel',
                            'agent', 'transport', 'system'
                        )
                    ),
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    action TEXT,
                    session_id TEXT,
                    operation_id TEXT,
                    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                    severity TEXT NOT NULL CHECK (
                        severity IN ('info', 'warning', 'error', 'fatal')
                    ),
                    correlation_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    occurred_at_epoch REAL NOT NULL,
                    closed_at TEXT,
                    close_reason TEXT CHECK (
                        close_reason IS NULL OR close_reason IN (
                            'acknowledged', 'recovered', 'entity_deleted'
                        )
                    ),
                    CHECK (
                        (closed_at IS NULL AND close_reason IS NULL) OR
                        (closed_at IS NOT NULL AND close_reason IS NOT NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_user_errors_open
                    ON user_errors(principal_id, seq DESC)
                    WHERE closed_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_user_errors_open_order_v2
                    ON user_errors(
                        principal_id, occurred_at_epoch DESC, error_id DESC
                    )
                    WHERE closed_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_user_errors_open_session
                    ON user_errors(principal_id, session_id, seq DESC)
                    WHERE closed_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_user_errors_request
                    ON user_errors(principal_id, request_id, seq DESC);
                """
            )

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            deadline = time.monotonic() + _INITIALIZE_TIMEOUT_SECONDS
            delay = 0.005
            while True:
                try:
                    self._initialize_schema()
                    break
                except sqlite3.OperationalError as exc:
                    busy = "locked" in str(exc).lower() or "busy" in str(exc).lower()
                    if not busy or time.monotonic() >= deadline:
                        raise
                    time.sleep(delay)
                    delay = min(delay * 2, 0.1)
            self._initialized = True

    @staticmethod
    def _prune(
        conn: sqlite3.Connection,
        principal_id: str,
        now: float,
    ) -> None:
        cutoff = now - RETENTION_SECONDS
        conn.execute(
            "DELETE FROM user_errors WHERE occurred_at_epoch < ?",
            (cutoff,),
        )
        conn.execute(
            "DELETE FROM user_errors WHERE principal_id = ? AND seq NOT IN ("
            "SELECT seq FROM user_errors WHERE principal_id = ? "
            "ORDER BY occurred_at_epoch DESC, error_id DESC LIMIT ?)",
            (
                principal_id,
                principal_id,
                MAX_RECORDS_PER_PRINCIPAL,
            ),
        )

    def record(self, record: UserErrorRecord, *, now: float | None = None) -> None:
        self.initialize()
        prune_now = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO user_errors (
                    error_id, principal_id, request_id, scope, code, message,
                    action, session_id, operation_id, retryable, severity,
                    correlation_id, occurred_at, occurred_at_epoch,
                    closed_at, close_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.error_id,
                    record.principal_id,
                    record.request_id,
                    record.scope,
                    record.code,
                    record.message,
                    record.action,
                    record.session_id,
                    record.operation_id,
                    int(record.retryable),
                    record.severity,
                    record.correlation_id,
                    record.occurred_at,
                    record.occurred_at_epoch,
                    record.closed_at,
                    record.close_reason,
                ),
            )
            self._prune(conn, record.principal_id, prune_now)
            conn.commit()

    def get(
        self,
        principal_id: str,
        error_id: str,
        *,
        now: float | None = None,
    ) -> UserErrorRecord | None:
        self.initialize()
        prune_now = time.time() if now is None else float(now)
        cutoff = prune_now - RETENTION_SECONDS
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM user_errors WHERE occurred_at_epoch < ?",
                (cutoff,),
            )
            row = conn.execute(
                "SELECT * FROM user_errors WHERE principal_id = ? AND error_id = ?",
                (principal_id, error_id),
            ).fetchone()
            conn.commit()
        return _record_from_row(row) if row is not None else None

    def list_open(
        self,
        principal_id: str,
        *,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
        now: float | None = None,
    ) -> UserErrorPage:
        self.initialize()
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("user error page limit must be an integer")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(
                f"user error page limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        page_size = limit
        before = None if cursor is None else _decode_cursor(cursor)
        prune_now = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._prune(conn, principal_id, prune_now)
            if before is None:
                rows = conn.execute(
                    "SELECT * FROM user_errors "
                    "WHERE principal_id = ? AND closed_at IS NULL "
                    "ORDER BY occurred_at_epoch DESC, error_id DESC LIMIT ?",
                    (principal_id, page_size + 1),
                ).fetchall()
            else:
                before_epoch, before_error_id = before
                rows = conn.execute(
                    "SELECT * FROM user_errors "
                    "WHERE principal_id = ? AND closed_at IS NULL AND ("
                    "occurred_at_epoch < ? OR "
                    "(occurred_at_epoch = ? AND error_id < ?)) "
                    "ORDER BY occurred_at_epoch DESC, error_id DESC LIMIT ?",
                    (
                        principal_id,
                        before_epoch,
                        before_epoch,
                        before_error_id,
                        page_size + 1,
                    ),
                ).fetchall()
            conn.commit()
        visible = rows[:page_size]
        next_cursor = (
            _encode_cursor(
                float(visible[-1]["occurred_at_epoch"]),
                str(visible[-1]["error_id"]),
            )
            if len(rows) > page_size and visible
            else None
        )
        return UserErrorPage(
            records=tuple(_record_from_row(row) for row in visible),
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
        self.initialize()
        prune_now = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._prune(conn, principal_id, prune_now)
            row = conn.execute(
                "SELECT * FROM user_errors WHERE principal_id = ? AND error_id = ?",
                (principal_id, error_id),
            ).fetchone()
            if row is not None and row["closed_at"] is None:
                conn.execute(
                    "UPDATE user_errors SET closed_at = ?, close_reason = ? "
                    "WHERE principal_id = ? AND error_id = ? AND closed_at IS NULL",
                    (occurred_at, "acknowledged", principal_id, error_id),
                )
                row = conn.execute(
                    "SELECT * FROM user_errors "
                    "WHERE principal_id = ? AND error_id = ?",
                    (principal_id, error_id),
                ).fetchone()
            conn.commit()
        return _record_from_row(row) if row is not None else None


def _record_from_row(row: sqlite3.Row) -> UserErrorRecord:
    return UserErrorRecord(
        principal_id=str(row["principal_id"]),
        error_id=str(row["error_id"]),
        request_id=row["request_id"],
        scope=str(row["scope"]),
        code=str(row["code"]),
        message=str(row["message"]),
        action=row["action"],
        session_id=row["session_id"],
        operation_id=row["operation_id"],
        retryable=bool(row["retryable"]),
        severity=str(row["severity"]),
        correlation_id=str(row["correlation_id"]),
        occurred_at=str(row["occurred_at"]),
        occurred_at_epoch=float(row["occurred_at_epoch"]),
        closed_at=row["closed_at"],
        close_reason=row["close_reason"],
    )
