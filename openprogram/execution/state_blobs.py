"""Execution-owned content-addressed state blobs for Agent checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .store import ExecutionConflict, ExecutionStore, MAX_AGENT_STATE_BLOB_BYTES
from .model import TERMINAL_EXECUTION_STATUSES


class StateBlobConflict(ExecutionConflict):
    pass


@dataclass(frozen=True)
class StateBlobRecord:
    ref: str
    sha256: str
    byte_length: int
    media_type: str
    schema_version: int


class ExecutionStateBlobStore:
    def __init__(self, executions: ExecutionStore):
        self.executions = executions

    def put(self, *, execution_id: str, attempt_id: str, name: str, payload: bytes | str, media_type: str, schema_version: int) -> StateBlobRecord:
        try:
            with self.executions._transaction() as connection:
                execution = self.executions._require_execution(connection, execution_id)
                if execution.current_attempt_id != attempt_id:
                    raise StateBlobConflict("stale_attempt", "state blob owner is stale")
                record = self.executions._put_state_blob_in_transaction(
                    connection, execution_id=execution_id, payload=payload,
                    media_type=media_type, schema_version=schema_version,
                )
                return StateBlobRecord(**record)
        except ExecutionConflict as exc:
            raise StateBlobConflict(exc.code, str(exc)) from exc

    def get(self, ref: str, *, execution_id: str) -> StateBlobRecord | None:
        try:
            value = self.executions.get_state_blob(execution_id, ref)
        except ExecutionConflict as exc:
            raise StateBlobConflict(exc.code, str(exc)) from exc
        if value is None:
            return None
        return StateBlobRecord(**{key: value[key] for key in StateBlobRecord.__dataclass_fields__})

    def list(self, execution_id: str) -> list[StateBlobRecord]:
        with self.executions._connect() as connection:
            rows = connection.execute(
                "SELECT ref, sha256, byte_length, media_type, schema_version "
                "FROM execution_state_blobs WHERE execution_id = ? ORDER BY ref", (execution_id,)
            ).fetchall()
        return [StateBlobRecord(**dict(row)) for row in rows]

    def owners(self, ref: str) -> set[str]:
        with self.executions._connect() as connection:
            rows = connection.execute(
                "SELECT execution_id FROM execution_state_blobs WHERE ref = ?", (ref,)
            ).fetchall()
        return {str(row["execution_id"]) for row in rows}

    def attach_ref(self, *, execution_id: str, ref: str, name: str, reference_kind: str = "checkpoint", reference_id: str = "") -> None:
        try:
            self.executions._validate_state_ref(ref)
            with self.executions._transaction() as connection:
                self.executions._require_execution(connection, execution_id)
                if connection.execute("SELECT 1 FROM execution_state_blobs WHERE execution_id = ? AND ref = ?", (execution_id, ref)).fetchone() is None:
                    raise StateBlobConflict("state_ref_invalid", "state ref is not owned by execution")
                connection.execute(
                    "INSERT OR IGNORE INTO execution_state_blob_refs "
                    "(execution_id, ref, name, reference_kind, reference_id, created_at) VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
                    (execution_id, ref, name, reference_kind, reference_id),
                )
        except ExecutionConflict as exc:
            raise StateBlobConflict(exc.code, str(exc)) from exc

    def detach_ref(self, *, execution_id: str, ref: str, reference_kind: str, reference_id: str) -> None:
        with self.executions._transaction() as connection:
            connection.execute(
                "DELETE FROM execution_state_blob_refs WHERE execution_id = ? AND ref = ? AND reference_kind = ? AND reference_id = ?",
                (execution_id, ref, reference_kind, reference_id),
            )

    def gc(self, *, execution_id: str) -> int:
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if execution.status not in TERMINAL_EXECUTION_STATUSES:
                return 0
            result = connection.execute(
                "DELETE FROM execution_state_blobs WHERE execution_id = ? AND ref NOT IN "
                "(SELECT ref FROM execution_state_blob_refs WHERE execution_id = ?)",
                (execution_id, execution_id),
            )
            return int(result.rowcount)
