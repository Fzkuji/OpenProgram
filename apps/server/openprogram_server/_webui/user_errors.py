"""Durable, bounded records for user-visible runtime failures."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


MAX_RECORDS_PER_PRINCIPAL = 1_000
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_PAGE_SIZE = 100
_INITIALIZE_TIMEOUT_SECONDS = 5.0


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
            "ORDER BY seq DESC LIMIT ?)",
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
        page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
        try:
            before_seq = int(cursor) if cursor is not None else None
        except (TypeError, ValueError):
            before_seq = None
        prune_now = time.time() if now is None else float(now)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._prune(conn, principal_id, prune_now)
            if before_seq is None:
                rows = conn.execute(
                    "SELECT * FROM user_errors "
                    "WHERE principal_id = ? AND closed_at IS NULL "
                    "ORDER BY seq DESC LIMIT ?",
                    (principal_id, page_size + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_errors "
                    "WHERE principal_id = ? AND closed_at IS NULL AND seq < ? "
                    "ORDER BY seq DESC LIMIT ?",
                    (principal_id, before_seq, page_size + 1),
                ).fetchall()
            conn.commit()
        visible = rows[:page_size]
        next_cursor = (
            str(visible[-1]["seq"])
            if len(rows) > page_size and visible
            else None
        )
        return UserErrorPage(
            records=tuple(_record_from_row(row) for row in visible),
            next_cursor=next_cursor,
        )


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
