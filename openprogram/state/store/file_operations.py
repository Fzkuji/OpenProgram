"""Durable idempotency records for project-file mutations.

The record is deliberately separate from the in-memory file query snapshots:
retries must remain identifiable after a worker restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from openprogram.state.store.record_log import RecordLog, record_log


_PROCESS_INSTANCE_ID = uuid.uuid4().hex

# Terminal receipts are retained long enough for normal idempotent replay,
# while the journal cannot grow without bound. In-flight and recovery records
# are intentionally excluded from both limits.
FILE_OPERATION_TERMINAL_TTL_SECONDS = 7 * 24 * 60 * 60
FILE_OPERATION_MAX_TERMINAL_RECORDS = 1024
_TERMINAL_STATUSES = ("completed", "conflict", "error")


def process_start_identity(pid: int | None = None) -> str | None:
    """Return a PID-reuse-resistant process start token when available."""
    from openprogram.host._platform.processes import process_start_token
    return process_start_token(os.getpid() if pid is None else pid)


# This process's own start token never changes, and reading it on macOS
# spawns `ps`; every file operation records it, so read it once per PID.
_current_process_start: tuple[int, str] | None = None


def current_owner_identity() -> tuple[str, int, str | None]:
    global _current_process_start
    pid = os.getpid()
    if _current_process_start is None or _current_process_start[0] != pid:
        start = process_start_identity(pid)
        if start is None:
            return _PROCESS_INSTANCE_ID, pid, None
        _current_process_start = (pid, start)
    return _PROCESS_INSTANCE_ID, pid, _current_process_start[1]


class FileOperationConflict(RuntimeError):
    pass


def fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encoded(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), sort_keys=True, separators=(",", ":"), default=str)


class FileOperationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._log: RecordLog = record_log(self.path)
        self.compact()

    @staticmethod
    def _by_identity(log: RecordLog, project_id: str, action: str, key: str) -> dict[str, Any] | None:
        for row in log.records.values():
            if (row["project_id"], row["action"], row["idempotency_key"]) == (project_id, action, key):
                return row
        return None

    def begin(self, project_id: str, action: str, key: str,
              request_fingerprint: str, *, payload: Mapping[str, Any] | None = None,
              before: Mapping[str, Any] | None = None,
              after: Mapping[str, Any] | None = None,
              owner_instance_id: str | None = None,
              owner_process_start: str | None = None) -> tuple[dict[str, Any], bool]:
        now = time.time()
        instance_id, owner_pid, process_start = current_owner_identity()
        with self._log.locked() as log:
            existing = self._by_identity(log, project_id, action, key)
            if existing is not None:
                if existing["fingerprint"] != request_fingerprint:
                    raise FileOperationConflict("IDEMPOTENCY_KEY_CONFLICT")
                return dict(existing), False
            row = {
                "project_id": project_id,
                "action": action,
                "idempotency_key": key,
                "fingerprint": request_fingerprint,
                "operation_id": f"fileop_{uuid.uuid4().hex}",
                "owner_pid": owner_pid,
                "owner_instance_id": owner_instance_id or instance_id,
                "owner_process_start": owner_process_start or process_start,
                "status": "in_flight",
                "phase": "prepared",
                "payload_json": _encoded(payload),
                "before_json": _encoded(before),
                "after_json": _encoded(after),
                "result_json": None,
                "created_at": now,
                "updated_at": now,
            }
            log.put(row["operation_id"], row)
            return dict(row), True

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._log.locked() as log:
            row = log.records.get(operation_id)
            return dict(row) if row is not None else None

    def find(self, project_id: str, action: str, key: str) -> dict[str, Any] | None:
        """Read a receipt by its idempotency identity without creating one."""
        with self._log.locked() as log:
            row = self._by_identity(log, project_id, action, key)
            return dict(row) if row is not None else None

    def compact(self, *, now: float | None = None) -> int:
        """Remove only old/excess terminal receipts.

        Recovery and in-flight rows remain durable regardless of age or
        journal size, so compaction cannot erase state that still needs
        operator or retry action.
        """
        cutoff = (time.time() if now is None else now) - FILE_OPERATION_TERMINAL_TTL_SECONDS
        with self._log.locked() as log:
            terminal = sorted(
                (row for row in log.records.values() if row["status"] in _TERMINAL_STATUSES),
                key=lambda row: (row["updated_at"], row["operation_id"]),
            )
            expired = [row for row in terminal if row["updated_at"] < cutoff]
            kept = terminal[len(expired):]
            excess = kept[:max(0, len(kept) - FILE_OPERATION_MAX_TERMINAL_RECORDS)]
            removed = [row["operation_id"] for row in (*expired, *excess)]
            log.delete(removed)
        return len(removed)

    def _change_in_flight(self, operation_id: str, **fields: Any) -> bool:
        with self._log.locked() as log:
            row = log.records.get(operation_id)
            if row is None or row["status"] != "in_flight":
                return False
            log.put(operation_id, {**row, **fields, "updated_at": time.time()})
            return True

    def claim_recovery(self, operation_id: str) -> bool:
        instance_id, owner_pid, process_start = current_owner_identity()
        return self._change_in_flight(
            operation_id, owner_pid=owner_pid, owner_instance_id=instance_id,
            owner_process_start=process_start,
        )

    def owned_by_current_process(self, operation_id: str) -> bool:
        instance_id, owner_pid, process_start = current_owner_identity()
        row = self.get(operation_id)
        return bool(row and row.get("status") == "in_flight"
                    and row.get("owner_instance_id") == instance_id
                    and row.get("owner_pid") == owner_pid
                    and row.get("owner_process_start") == process_start)

    def finish(self, operation_id: str, result: Mapping[str, Any], *,
               status: str = "completed", phase: str = "completed") -> None:
        self._change_in_flight(
            operation_id, status=status, phase=phase,
            result_json=json.dumps(dict(result), sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, default=str),
        )

    def complete(self, operation_id: str, result: Mapping[str, Any]) -> None:
        self.finish(operation_id, result)

    def mark_applying(self, operation_id: str) -> None:
        self._change_in_flight(operation_id, phase="applying")

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
    from openprogram.state.paths import get_state_dir
    return FileOperationStore(get_state_dir() / "file_operations.jsonl")
