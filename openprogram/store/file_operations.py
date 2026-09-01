"""Durable idempotency records for project-file mutations.

The record is deliberately separate from the in-memory file query snapshots:
retries must remain identifiable after a worker restart.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


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
              after: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        now = time.time()
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
                "fingerprint, operation_id, status, phase, payload_json, before_json, "
                "after_json, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'in_flight', 'prepared', ?, ?, ?, NULL, ?, ?)",
                (project_id, action, key, request_fingerprint, operation_id,
                 json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), default=str),
                 json.dumps(dict(before or {}), sort_keys=True, separators=(",", ":"), default=str),
                 json.dumps(dict(after or {}), sort_keys=True, separators=(",", ":"), default=str),
                 now, now),
            )
            row = db.execute(
                "SELECT * FROM file_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return dict(row), True

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
