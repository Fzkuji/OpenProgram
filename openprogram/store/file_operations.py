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
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS file_operations (
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (project_id, action, idempotency_key)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def begin(self, project_id: str, action: str, key: str,
              request_fingerprint: str) -> tuple[dict[str, Any], bool]:
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
                "fingerprint, operation_id, status, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'in_flight', NULL, ?, ?)",
                (project_id, action, key, request_fingerprint, operation_id, now, now),
            )
            row = db.execute(
                "SELECT * FROM file_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            return dict(row), True

    def complete(self, operation_id: str, result: Mapping[str, Any]) -> None:
        now = time.time()
        with self._connect() as db:
            db.execute(
                "UPDATE file_operations SET status='completed', result_json=?, "
                "updated_at=? WHERE operation_id=? AND status='in_flight'",
                (json.dumps(dict(result), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str), now, operation_id),
            )

    @staticmethod
    def replay(row: Mapping[str, Any]) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row.get("result_json") else {}
        if not isinstance(result, dict):
            result = {}
        result["operation_id"] = row["operation_id"]
        if row["status"] != "completed":
            result.setdefault("in_flight", True)
            result.setdefault("status", "in_flight")
        return result


def default_file_operation_store() -> FileOperationStore:
    from openprogram.paths import get_state_dir
    return FileOperationStore(get_state_dir() / "file_operations.db")
