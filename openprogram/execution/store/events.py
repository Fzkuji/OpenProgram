"""Execution persistence: events."""
from __future__ import annotations


import json


import sqlite3

import time

import uuid


from contextlib import closing



from typing import Any, Collection, Mapping


from .._schema import PROJECTION_KINDS, SCHEMA_VERSION

from ..model import AuditEvent, ExecutionEvent, EventCursor, EventReplay, ExecutionRecord, _json


from .shared import (
    ExecutionConflict,
    _object,
    _projection_event_written,
)

class EventsOperations:
    def list_events(self, execution_id: str) -> list[ExecutionEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM execution_events WHERE execution_id = ? "
                "ORDER BY sequence",
                (execution_id,),
            )
            return [self._event(row) for row in rows]


    def read_event_replay(
        self, execution_id: str, *, after_sequence: int
    ) -> EventReplay:
        """Read one exact execution's contiguous public event stream.

        SQLite event ids are global because the projection outbox references
        them.  Public cursors instead use ``execution_sequence`` so activity
        in a different execution cannot manufacture a false gap.
        """
        if type(after_sequence) is not int or after_sequence < 0:
            raise ExecutionConflict("invalid_cursor", "after_sequence must be a non-negative integer")
        with closing(self._connect()) as connection:
            # One WAL read snapshot binds lifecycle state to the event cursor.
            # A later writer must not advance bounds or status independently.
            connection.execute("BEGIN")
            execution = self._get_execution(connection, execution_id)
            if execution is None:
                raise ExecutionConflict("not_found", "execution does not exist")
            bounds = connection.execute(
                "SELECT MIN(execution_sequence) AS first_sequence, "
                "MAX(execution_sequence) AS last_sequence "
                "FROM execution_events WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if bounds is None or bounds["last_sequence"] is None:
                raise ExecutionConflict("not_found", "execution does not exist")
            first = int(bounds["first_sequence"])
            last = int(bounds["last_sequence"])
            recovery = None
            if after_sequence > last:
                recovery = "cursor_ahead"
                rows = ()
            elif after_sequence < first - 1:
                recovery = "cursor_expired"
                rows = ()
            else:
                rows = connection.execute(
                    "SELECT * FROM execution_events WHERE execution_id = ? "
                    "AND execution_sequence > ? ORDER BY execution_sequence",
                    (execution_id, after_sequence),
                ).fetchall()
                expected = after_sequence + 1
                for row in rows:
                    if int(row["execution_sequence"]) != expected:
                        recovery = "cursor_gap"
                        rows = ()
                        break
                    expected += 1
        return EventReplay(
            execution_id=execution_id,
            execution=execution,
            events=tuple(self._event(row) for row in rows),
            cursor=EventCursor(
                execution_id=execution_id,
                next_sequence=last + 1,
                snapshot_status_version=execution.status_version,
            ),
            recovery=recovery,
        )


    def get_event(self, execution_id: str, sequence: int) -> ExecutionEvent | None:
        """Return one event by its global sequence within the exact execution."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_events WHERE execution_id = ? AND sequence = ?",
                (execution_id, sequence),
            ).fetchone()
        return self._event(row) if row is not None else None


    def execution_snapshot_at(
        self, execution_id: str, sequence: int
    ) -> ExecutionRecord | None:
        """Read the nearest persisted execution snapshot at or before an event."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM execution_events "
                "WHERE execution_id = ? AND sequence <= ? "
                "AND kind IN ('execution.created', 'execution.updated') "
                "ORDER BY sequence DESC LIMIT 1",
                (execution_id, sequence),
            ).fetchone()
        return (
            ExecutionRecord.from_dict(_object(row["payload_json"])["record"])
            if row is not None
            else None
        )


    def rebuild_execution(self, execution_id: str) -> ExecutionRecord | None:
        record = None
        for event in self.list_events(execution_id):
            if event.kind in {"execution.created", "execution.updated"}:
                record = ExecutionRecord.from_dict(event.payload["record"])
        return record


    @staticmethod
    def _event(row: sqlite3.Row) -> ExecutionEvent:
        return ExecutionEvent(
            sequence=int(row["sequence"]),
            execution_id=str(row["execution_id"]),
            execution_version=row["execution_version"],
            command_id=row["command_id"],
            kind=str(row["kind"]),
            payload=_object(row["payload_json"]),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
            execution_sequence=int(row["execution_sequence"]),
        )


    def append_audit_event(
        self,
        *,
        execution_id: str,
        actor: Mapping[str, Any],
        action: str,
        result: str,
        surface: str,
        payload: Mapping[str, Any] | None = None,
        command_id: str | None = None,
        draft_id: str | None = None,
        wait_id: str | None = None,
        correlation_id: str | None = None,
        source_version: int | None = None,
        checkpoint_id: str | None = None,
        reason_code: str | None = None,
        evidence_refs: Collection[str] = (),
        project_binding: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        """Append a redacted audit record; the ledger is never updated."""
        with self._transaction() as connection:
            execution = self._require_execution(connection, execution_id)
            binding = dict(project_binding or self._project_binding(execution))
            return self._append_audit_event(
                connection,
                execution=execution,
                actor=actor,
                action=action,
                result=result,
                surface=surface,
                payload=payload,
                command_id=command_id,
                draft_id=draft_id,
                wait_id=wait_id,
                correlation_id=correlation_id,
                source_version=source_version,
                checkpoint_id=checkpoint_id,
                reason_code=reason_code,
                evidence_refs=evidence_refs,
                project_binding=binding,
            )


    def list_audit_events(
        self, execution_id: str, *, actor: Mapping[str, Any],
        conversation_session_id: str | None = None,
    ) -> list[AuditEvent]:
        execution = self.get_execution(execution_id)
        if execution is None:
            from ..authorization import ExecutionAuthorizationError
            raise ExecutionAuthorizationError("execution is not visible")
        from ..authorization import authorize_execution_action

        if conversation_session_id is None:
            authorize_execution_action(
                actor, "audit.read", execution, self._project_binding(execution)
            )
        else:
            from ..conversation_scope import authorize_conversation_execution

            authorize_conversation_execution(actor, "audit.read", execution, store=self,
                                             session_id=conversation_session_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM execution_audit_events WHERE execution_id = ? "
                "ORDER BY sequence",
                (execution_id,),
            ).fetchall()
        return [self._audit_event(row) for row in rows]


    @staticmethod
    def _project_binding(execution: ExecutionRecord) -> dict[str, str]:
        try:
            from openprogram.store.project import project_for_session
            project = project_for_session(execution.session_id)
            project_id = project.id if project is not None else "default"
        except Exception:
            project_id = "default"
        return {"project_id": project_id, "session_id": execution.session_id}


    @staticmethod
    def _audit_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            audit_id=str(row["audit_id"]),
            sequence=int(row["sequence"]),
            execution_id=str(row["execution_id"]),
            command_id=row["command_id"],
            draft_id=row["draft_id"],
            wait_id=row["wait_id"],
            correlation_id=row["correlation_id"],
            actor_binding=_object(row["actor_json"]),
            surface=str(row["surface"]),
            action=str(row["action"]),
            policy_version=str(row["policy_version"]),
            project_binding=_object(row["project_binding_json"]),
            source_version=row["source_version"],
            checkpoint_id=row["checkpoint_id"],
            result=str(row["result"]),
            reason_code=row["reason_code"],
            redacted_payload=_object(row["redacted_payload_json"]),
            evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))),
            created_at=float(row["created_at"]),
        )


    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        kind: str,
        payload: Mapping[str, Any],
        created_at: float,
        execution_version: int | None = None,
        command_id: str | None = None,
    ) -> int:
        execution_sequence = int(connection.execute(
            "SELECT COALESCE(MAX(execution_sequence), 0) + 1 "
            "FROM execution_events WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()[0])
        cursor = connection.execute(
            "INSERT INTO execution_events "
            "(execution_id, execution_sequence, execution_version, command_id, kind, payload_json, "
            "created_at, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution_id,
                execution_sequence,
                execution_version,
                command_id,
                kind,
                _json(payload),
                created_at,
                SCHEMA_VERSION,
            ),
        )
        sequence = int(cursor.lastrowid)
        _projection_event_written.set(True)
        # Every canonical event is fanned out to the fixed projection set in
        # the same SQLite transaction.  Consumers are independently retryable.
        for projection_kind in PROJECTION_KINDS:
            connection.execute(
                "INSERT INTO execution_projection_outbox ("
                "outbox_id, event_sequence, execution_id, projection_kind, "
                "dedupe_key, payload_ref, state, claim_owner, claim_expires_at, "
                "attempts, available_at, delivered_at, last_error) VALUES "
                "(?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, 0, ?, NULL, NULL)",
                (
                    f"outbox_{sequence}_{projection_kind}",
                    sequence,
                    execution_id,
                    projection_kind,
                    f"execution-event:{sequence}:{projection_kind}",
                    f"execution-event:{sequence}",
                    created_at,
                ),
            )
        return sequence


    @staticmethod
    def _append_audit_event(
        connection: sqlite3.Connection,
        *,
        execution: ExecutionRecord,
        actor: Mapping[str, Any],
        action: str,
        result: str,
        surface: str,
        payload: Mapping[str, Any] | None,
        command_id: str | None,
        draft_id: str | None,
        wait_id: str | None,
        correlation_id: str | None,
        source_version: int | None,
        checkpoint_id: str | None,
        reason_code: str | None,
        evidence_refs: Collection[str],
        project_binding: Mapping[str, Any],
    ) -> AuditEvent:
        from ..audit import redact_audit_payload
        from ..authorization import POLICY_VERSION
        from openprogram.agent.authority import normalize_authority

        actor_binding = normalize_authority(actor)
        if not actor_binding:
            actor_binding = {"authority_tier": "runtime", "speaker_id": "runtime/internal"}
        else:
            # Authority normalization intentionally keeps the stable identity
            # fields only.  Control records additionally carry trusted
            # transport/scope metadata so an audit reader can reconstruct the
            # authorization decision.  Copy only the fixed safe fields;
            # arbitrary actor data (including secrets) never enters the audit.
            trusted_surface = actor.get("surface")
            if isinstance(trusted_surface, str) and trusted_surface:
                actor_binding["surface"] = trusted_surface
            for field in ("project_ids", "session_ids", "execution_actions"):
                value = actor.get(field)
                if isinstance(value, (list, tuple, frozenset, set)) and all(
                    isinstance(item, str) and item for item in value
                ):
                    actor_binding[field] = tuple(str(item) for item in value)
            for field in ("resolved_project_id", "resolved_session_id"):
                value = actor.get(field)
                if isinstance(value, str) and value:
                    actor_binding[field] = value
        audit_id = f"audit_{uuid.uuid4().hex}"
        now = time.time()
        redacted_payload = redact_audit_payload(payload)
        refs = tuple(str(ref) for ref in evidence_refs)
        cursor = connection.execute(
            "INSERT INTO execution_audit_events ("
            "audit_id, execution_id, command_id, draft_id, wait_id, correlation_id, "
            "actor_json, surface, action, policy_version, project_binding_json, "
            "source_version, checkpoint_id, result, reason_code, redacted_payload_json, "
            "evidence_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id, execution.execution_id, command_id, draft_id, wait_id,
                correlation_id, _json(actor_binding), str(surface), str(action),
                POLICY_VERSION, _json(dict(project_binding)), source_version,
                checkpoint_id, str(result), reason_code, _json(redacted_payload),
                _json(list(refs)), now,
            ),
        )
        return AuditEvent(
            audit_id=audit_id,
            sequence=int(cursor.lastrowid),
            execution_id=execution.execution_id,
            command_id=command_id,
            draft_id=draft_id,
            wait_id=wait_id,
            correlation_id=correlation_id,
            actor_binding=actor_binding,
            surface=str(surface),
            action=str(action),
            policy_version=POLICY_VERSION,
            project_binding=dict(project_binding),
            source_version=source_version,
            checkpoint_id=checkpoint_id,
            result=str(result),
            reason_code=reason_code,
            redacted_payload=redacted_payload,
            evidence_refs=refs,
            created_at=now,
        )

