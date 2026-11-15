"""Execution-owned content-addressed state blobs for Agent checkpoints."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .store import ExecutionConflict, ExecutionStore
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
            rows = connection.rows("execution_state_blobs", order="ref", execution_id=execution_id)
        return [StateBlobRecord(**{key: row[key] for key in StateBlobRecord.__dataclass_fields__}) for row in rows]

    def owners(self, ref: str) -> set[str]:
        with self.executions._connect() as connection:
            rows = connection.rows("execution_state_blobs", ref=ref)
        return {str(row["execution_id"]) for row in rows}

    def attach_ref(self, *, execution_id: str, ref: str, name: str, reference_kind: str = "checkpoint", reference_id: str = "") -> None:
        try:
            self.executions._validate_state_ref(ref)
            with self.executions._transaction() as connection:
                self.executions._require_execution(connection, execution_id)
                if connection.get("execution_state_blobs", execution_id, ref) is None:
                    raise StateBlobConflict("state_ref_invalid", "state ref is not owned by execution")
                connection.insert(
                    "execution_state_blob_refs",
                    {
                        "execution_id": execution_id,
                        "ref": ref,
                        "name": name,
                        "reference_kind": reference_kind,
                        "reference_id": reference_id,
                        "created_at": float(int(time.time())),
                    },
                    on_conflict="ignore",
                )
        except ExecutionConflict as exc:
            raise StateBlobConflict(exc.code, str(exc)) from exc

    def detach_ref(self, *, execution_id: str, ref: str, reference_kind: str, reference_id: str) -> None:
        with self.executions._transaction() as connection:
            connection.delete(
                "execution_state_blob_refs",
                execution_id=execution_id,
                ref=ref,
                reference_kind=reference_kind,
                reference_id=reference_id,
            )

    def gc(self, *, execution_id: str) -> int:
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if execution.status not in TERMINAL_EXECUTION_STATUSES:
                return 0
            referenced = {row["ref"] for row in connection.rows("execution_state_blob_refs", execution_id=execution_id)}
            return connection.delete(
                "execution_state_blobs",
                where=lambda blob: blob["ref"] not in referenced,
                execution_id=execution_id,
            )
