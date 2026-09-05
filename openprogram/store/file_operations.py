"""Durable idempotency records for project-file mutations.

The record is deliberately separate from the in-memory file query snapshots:
retries must remain identifiable after a worker restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


_PROCESS_INSTANCE_ID = uuid.uuid4().hex

# Terminal receipts are retained long enough for normal idempotent replay,
# while the journal cannot grow without bound. In-flight and recovery records
# are intentionally excluded from both limits.
FILE_OPERATION_TERMINAL_TTL_SECONDS = 7 * 24 * 60 * 60
FILE_OPERATION_MAX_TERMINAL_RECORDS = 1024
_TERMINAL_STATUSES = ("completed", "conflict", "error")


def process_start_identity(pid: int | None = None) -> str | None:
    """Return a PID-reuse-resistant process start token when available."""
    from openprogram._compat import process_start_token
    return process_start_token(os.getpid() if pid is None else pid)


def current_owner_identity() -> tuple[str, int, str | None]:
    return _PROCESS_INSTANCE_ID, os.getpid(), process_start_identity()


class FileOperationConflict(RuntimeError):
    pass


def fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FileOperationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS file_operations (
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    owner_pid INTEGER,
                    owner_instance_id TEXT,
                    owner_process_start TEXT,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'prepared',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (project_id, action, idempotency_key)
                )
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(file_operations)")}
            for name, definition in (
                ("phase", "TEXT NOT NULL DEFAULT 'prepared'"),
                ("payload_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("before_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("after_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("owner_pid", "INTEGER"),
                ("owner_instance_id", "TEXT"),
                ("owner_process_start", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE file_operations ADD COLUMN {name} {definition}")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self.compact()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=5.0)
        for sidecar in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                sidecar.chmod(0o600)
            except OSError:
                pass
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def begin(self, project_id: str, action: str, key: str,
              request_fingerprint: str, *, payload: Mapping[str, Any] | None = None,
              before: Mapping[str, Any] | None = None,
              after: Mapping[str, Any] | None = None,
              owner_instance_id: str | None = None,
              owner_process_start: str | None = None) -> tuple[dict[str, Any], bool]:
        now = time.time()
        instance_id, owner_pid, process_start = current_owner_identity()
        owner_instance_id = owner_instance_id or instance_id
        owner_process_start = owner_process_start or process_start
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM file_operations WHERE project_id=? AND action=? "
                "AND idempotency_key=?", (project_id, action, key),
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != request_fingerprint:
                    raise FileOperationConflict("IDEMPOTENCY_KEY_CONFLICT")
                return dict(row), False
            operation_id = f"fileop_{uuid.uuid4().hex}"
            db.execute(
                "INSERT INTO file_operations(project_id, action, idempotency_key, "
                "fingerprint, operation_id, status, phase, owner_pid, owner_instance_id, owner_process_start, payload_json, before_json, "
                "after_json, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'in_flight', 'prepared', ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (project_id, action, key, request_fingerprint, operation_id,
                 owner_pid, owner_instance_id, owner_process_start,
                 json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), default=str),
                 json.dumps(dict(before or {}), sort_keys=True, separators=(",", ":"), default=str),
                 json.dumps(dict(after or {}), sort_keys=True, separators=(",", ":"), default=str),
                 now, now),
            )
            row = db.execute(
                "SELECT * FROM file_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return dict(row), True

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM file_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def find(self, project_id: str, action: str, key: str) -> dict[str, Any] | None:
        """Read a receipt by its idempotency identity without creating one."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM file_operations WHERE project_id=? AND action=? "
                "AND idempotency_key=?", (project_id, action, key),
            ).fetchone()
        return dict(row) if row is not None else None

    def compact(self, *, now: float | None = None) -> int:
        """Remove only old/excess terminal receipts.

        Recovery and in-flight rows remain durable regardless of age or
        journal size, so compaction cannot erase state that still needs
        operator or retry action.
        """
        cutoff = (time.time() if now is None else now) - FILE_OPERATION_TERMINAL_TTL_SECONDS
        placeholders = ",".join("?" for _ in _TERMINAL_STATUSES)
        deleted = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                f"DELETE FROM file_operations WHERE status IN ({placeholders}) AND updated_at < ?",
                (*_TERMINAL_STATUSES, cutoff),
            )
            deleted += cursor.rowcount if cursor.rowcount > 0 else 0
            count = db.execute(
                f"SELECT COUNT(*) FROM file_operations WHERE status IN ({placeholders})",
                _TERMINAL_STATUSES,
            ).fetchone()[0]
            excess = max(0, count - FILE_OPERATION_MAX_TERMINAL_RECORDS)
            if excess:
                rows = db.execute(
                    f"SELECT operation_id FROM file_operations WHERE status IN ({placeholders}) "
                    "ORDER BY updated_at ASC, operation_id ASC LIMIT ?",
                    (*_TERMINAL_STATUSES, excess),
                ).fetchall()
                for row in rows:
                    cursor = db.execute(
                        "DELETE FROM file_operations WHERE operation_id=?", (row[0],)
                    )
                    if cursor.rowcount > 0:
                        deleted += cursor.rowcount
        return deleted

    def claim_recovery(self, operation_id: str) -> bool:
        instance_id, owner_pid, process_start = current_owner_identity()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE file_operations SET owner_pid=?, owner_instance_id=?, "
                "owner_process_start=?, updated_at=? "
                "WHERE operation_id=? AND status='in_flight'",
                (owner_pid, instance_id, process_start, time.time(), operation_id),
            )
        return cursor.rowcount == 1

    def owned_by_current_process(self, operation_id: str) -> bool:
        instance_id, owner_pid, process_start = current_owner_identity()
        row = self.get(operation_id)
        return bool(row and row.get("status") == "in_flight"
                    and row.get("owner_instance_id") == instance_id
                    and row.get("owner_pid") == owner_pid
                    and row.get("owner_process_start") == process_start)

    def finish(self, operation_id: str, result: Mapping[str, Any], *,
               status: str = "completed", phase: str = "completed") -> None:
        now = time.time()
        with self._connect() as db:
            db.execute(
                "UPDATE file_operations SET status=?, phase=?, result_json=?, "
                "updated_at=? WHERE operation_id=? AND status='in_flight'",
                (status, phase,
                 json.dumps(dict(result), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str), now, operation_id),
            )

    def complete(self, operation_id: str, result: Mapping[str, Any]) -> None:
        self.finish(operation_id, result)

    def mark_applying(self, operation_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE file_operations SET phase='applying', updated_at=? "
                "WHERE operation_id=? AND status='in_flight'",
                (time.time(), operation_id),
            )

    @staticmethod
    def replay(row: Mapping[str, Any]) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row.get("result_json") else {}
        if not isinstance(result, dict):
            result = {}
        result["operation_id"] = row["operation_id"]
        if row["status"] not in {"completed", "recovery_required", "conflict", "error"}:
            result.setdefault("in_flight", True)
            result.setdefault("status", row.get("phase") or "in_flight")
        return result


def default_file_operation_store() -> FileOperationStore:
    from openprogram.paths import get_state_dir
    return FileOperationStore(get_state_dir() / "file_operations.db")
