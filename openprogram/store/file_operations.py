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


def process_start_identity(pid: int | None = None) -> str | None:
    """Return a PID-reuse-resistant process start token when available."""
    pid = os.getpid() if pid is None else pid
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text(encoding="ascii").split()
        if len(fields) > 21:
            return f"proc:{fields[21]}"
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        import subprocess
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            check=False, capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return f"ps:{value}" if result.returncode == 0 and value else None


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
