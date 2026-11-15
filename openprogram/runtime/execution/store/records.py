"""Execution persistence: records."""
from __future__ import annotations


import json

from typing import Any, Mapping



from ..model import CapabilitySet, CommandKind, CommandStatus, ControlCommand, ExecutionInputRecord, ExecutionRecord, ExecutionStatus, RevisionRecord, RunRecord, _json


from .shared import (
    ExecutionConflict,
    _object,
)
from .run_tables import TableHandle

class RecordsOperations:
    @staticmethod
    def _insert_execution(
        connection: TableHandle, record: ExecutionRecord
    ) -> None:
        connection.insert("executions", {
            "execution_id": record.execution_id,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "parent_execution_id": record.parent_execution_id,
            "source_checkpoint_id": record.source_checkpoint_id,
            "revision_id": record.revision_id,
            "status": record.status.value,
            "status_version": record.status_version,
            "reason_code": record.reason_code,
            "current_attempt_id": record.current_attempt_id,
            "owner_lease_json": _json(record.owner_lease),
            "checkpoint_head_id": record.checkpoint_head_id,
            "safe_point_json": _json(record.safe_point),
            "capabilities_json": _json(record.capabilities.to_dict()),
            "effect_summary_json": _json(record.effect_summary),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "terminal_at": record.terminal_at,
        })


    @staticmethod
    def _record(row: Mapping[str, Any]) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=str(row["execution_id"]),
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            revision_id=str(row["revision_id"]),
            parent_execution_id=row["parent_execution_id"],
            source_checkpoint_id=row["source_checkpoint_id"],
            status=ExecutionStatus(row["status"]),
            status_version=int(row["status_version"]),
            reason_code=row["reason_code"],
            current_attempt_id=row["current_attempt_id"],
            owner_lease=_object(row["owner_lease_json"]),
            checkpoint_head_id=row["checkpoint_head_id"],
            safe_point=_object(row["safe_point_json"]),
            capabilities=CapabilitySet.from_dict(json.loads(row["capabilities_json"])),
            effect_summary=_object(row["effect_summary_json"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            terminal_at=(
                float(row["terminal_at"]) if row["terminal_at"] is not None else None
            ),
        )


    @staticmethod
    def _execution_input(row: Mapping[str, Any]) -> ExecutionInputRecord:
        return ExecutionInputRecord(
            execution_id=str(row["execution_id"]),
            input_ref=str(row["input_ref"]),
            input_hash=str(row["input_hash"]),
            entrypoint=str(row["entrypoint"]),
            session_id=str(row["session_id"]),
            user_message_id=row["user_message_id"],
            assistant_message_id=row["assistant_message_id"],
            trusted_actor=_object(row["trusted_actor_json"]),
            config_snapshot_ref=str(row["config_snapshot_ref"]),
            created_at=float(row["created_at"]),
        )


    @staticmethod
    def _resource_intent(row: Mapping[str, Any] | None) -> dict[str, Any]:
        if row is None:
            raise ExecutionConflict("not_found", "resource intent not found")
        return {
            "intent_id": str(row["intent_id"]),
            "execution_id": str(row["execution_id"]),
            "kind": str(row["kind"]),
            "idempotency_key": str(row["idempotency_key"]),
            "fingerprint": str(row["fingerprint"]),
            "admission_id": row["admission_id"],
            "attempt_id": row["attempt_id"],
            "generation": row["generation"],
            "resource_lease_generation": row["resource_lease_generation"],
            "payload": _object(row["payload_json"]),
            "state": str(row["state"]),
            "claim_owner": row["claim_owner"],
            "claim_expires_at": row["claim_expires_at"],
            "attempts": int(row["attempts"]),
            "result": _object(row["result_json"]),
            "last_error": row["last_error"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "completed_at": row["completed_at"],
        }


    @staticmethod
    def _command(row: Mapping[str, Any]) -> ControlCommand:
        return ControlCommand(
            command_id=str(row["command_id"]),
            execution_id=str(row["execution_id"]),
            expected_version=int(row["expected_version"]),
            kind=CommandKind(row["kind"]),
            payload=_object(row["payload_json"]),
            actor=_object(row["actor_json"]),
            status=CommandStatus(row["status"]),
            submitted_at=float(row["submitted_at"]),
            updated_at=float(row["updated_at"]),
            result_version=row["result_version"],
            rejection_code=row["rejection_code"],
            result_json=_object(row["result_json"]),
        )


    def _get_execution(
        self, connection: TableHandle, execution_id: str
    ) -> ExecutionRecord | None:
        self.tables.ensure_execution(execution_id)
        row = connection.get("executions", execution_id)
        return self._record(row) if row is not None else None


    def _require_execution(
        self, connection: TableHandle, execution_id: str
    ) -> ExecutionRecord:
        record = self._get_execution(connection, execution_id)
        if record is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        return record


    def _get_command(
        self, connection: TableHandle, command_id: str
    ) -> ControlCommand | None:
        row = connection.get("commands", command_id)
        return self._command(row) if row is not None else None


    @staticmethod
    def _revision(row: Mapping[str, Any]) -> RevisionRecord:
        return RevisionRecord(
            revision_id=str(row["revision_id"]),
            parent_revision_id=row["parent_revision_id"],
            content_hash=str(row["content_hash"]),
            manifest=_object(row["manifest_json"]),
            created_at=float(row["created_at"]),
        )


    def _get_revision(
        self, connection: TableHandle, revision_id: str
    ) -> RevisionRecord | None:
        row = connection.get("revisions", revision_id)
        return self._revision(row) if row is not None else None


    @staticmethod
    def _get_run(connection: TableHandle, run_id: str) -> RunRecord | None:
        row = connection.get("runs", run_id)
        if row is None:
            return None
        return RunRecord(
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            created_at=float(row["created_at"]),
        )

