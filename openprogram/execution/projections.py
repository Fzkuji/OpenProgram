"""Idempotent read models for fixed canonical execution projections."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Mapping

from ._schema import PROJECTION_KINDS
from .model import ExecutionEvent, ExecutionRecord
from .outbox import ProjectionOutboxRecord


_RUNNING_STATUSES = {
    "queued",
    "running",
    "pausing",
    "cancelling",
    "reconciliation_required",
}


@dataclass(frozen=True)
class ExecutionProjectionRecord:
    projection_kind: str
    event_sequence: int
    execution_id: str
    session_id: str
    status: str
    payload: Mapping[str, Any]
    created_at: float


class ExecutionProjectionReadModel:
    """Replayable read-only projections backed by the execution database.

    This class never changes canonical execution tables.  A retry after a
    process crash inserts the same immutable event row at most once and only
    advances the per-projection current snapshot.
    """

    def __init__(self, store):
        self.store = store

    def apply(self, item: ProjectionOutboxRecord, *, expected_kind: str | None = None) -> None:
        if expected_kind is not None and item.projection_kind != expected_kind:
            raise ValueError("projection handler received the wrong kind")
        if item.projection_kind not in PROJECTION_KINDS:
            raise ValueError(f"unsupported projection kind: {item.projection_kind}")
        event, execution = self._snapshot_at_event(item)
        payload = self._payload(item.projection_kind, event, execution)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO execution_projection_events "
                "(projection_kind, event_sequence, execution_id, session_id, status, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item.projection_kind,
                    event.sequence,
                    execution.execution_id,
                    execution.session_id,
                    execution.status.value,
                    encoded,
                    event.created_at,
                ),
            )
            connection.execute(
                "INSERT INTO execution_projection_current "
                "(projection_kind, execution_id, event_sequence, session_id, status, "
                "payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(projection_kind, execution_id) DO UPDATE SET "
                "event_sequence = excluded.event_sequence, session_id = excluded.session_id, "
                "status = excluded.status, payload_json = excluded.payload_json, "
                "updated_at = excluded.updated_at "
                "WHERE excluded.event_sequence > execution_projection_current.event_sequence",
                (
                    item.projection_kind,
                    execution.execution_id,
                    event.sequence,
                    execution.session_id,
                    execution.status.value,
                    encoded,
                    event.created_at,
                ),
            )
        if item.projection_kind == "ui":
            # The durable snapshot above remains the reconnect source of
            # truth.  This frame only updates already-connected clients.
            from openprogram.events import emit_ws_frame

            emit_ws_frame({"type": "execution.updated", "data": payload})

    def get_current(
        self, projection_kind: str, execution_id: str
    ) -> ExecutionProjectionRecord | None:
        self._validate_kind(projection_kind)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT projection_kind, event_sequence, execution_id, session_id, status, "
                "payload_json, updated_at FROM execution_projection_current "
                "WHERE projection_kind = ? AND execution_id = ?",
                (projection_kind, execution_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def list_events(
        self, projection_kind: str, execution_id: str
    ) -> list[ExecutionProjectionRecord]:
        self._validate_kind(projection_kind)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT projection_kind, event_sequence, execution_id, session_id, status, "
                "payload_json, created_at FROM execution_projection_events "
                "WHERE projection_kind = ? AND execution_id = ? ORDER BY event_sequence",
                (projection_kind, execution_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_running(self, *, session_id: str | None = None) -> list[ExecutionProjectionRecord]:
        values: list[Any] = ["ui", *_RUNNING_STATUSES]
        query = (
            "SELECT projection_kind, event_sequence, execution_id, session_id, status, "
            "payload_json, updated_at FROM execution_projection_current "
            "WHERE projection_kind = ? AND status IN ("
            + ", ".join("?" for _ in _RUNNING_STATUSES)
            + ")"
        )
        if session_id is not None:
            query += " AND session_id = ?"
            values.append(session_id)
        query += " ORDER BY updated_at DESC, execution_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._record(row) for row in rows]

    def _snapshot_at_event(
        self, item: ProjectionOutboxRecord
    ) -> tuple[ExecutionEvent, ExecutionRecord]:
        record = None
        event_at_sequence = None
        for event in self.store.list_events(item.execution_id):
            if event.sequence > item.event_sequence:
                break
            if event.kind in {"execution.created", "execution.updated"}:
                record = ExecutionRecord.from_dict(event.payload["record"])
            if event.sequence == item.event_sequence:
                event_at_sequence = event
                break
        if event_at_sequence is None:
            raise ValueError("projection outbox event is missing")
        if record is None:
            raise ValueError("projection event has no canonical execution snapshot")
        return event_at_sequence, record

    def _payload(
        self,
        projection_kind: str,
        event: ExecutionEvent,
        execution: ExecutionRecord,
    ) -> dict[str, Any]:
        source = self.store.get_execution_input(execution.execution_id)
        entrypoint = source.entrypoint if source is not None else None
        payload: dict[str, Any] = {
            "event": {
                "sequence": event.sequence,
                "kind": event.kind,
                "execution_version": event.execution_version,
                "command_id": event.command_id,
                "created_at": event.created_at,
            },
            "execution": execution.to_dict(),
            "input": {
                "entrypoint": entrypoint,
                "user_message_id": source.user_message_id if source else None,
                "assistant_message_id": source.assistant_message_id if source else None,
            },
        }
        if projection_kind == "dag":
            payload["dag"] = {
                "run_id": execution.run_id,
                "parent_execution_id": execution.parent_execution_id,
                "source_checkpoint_id": execution.source_checkpoint_id,
                "revision_id": execution.revision_id,
            }
        elif projection_kind == "job":
            payload["job"] = {
                "execution_id": execution.execution_id,
                "status": execution.status.value,
                "entrypoint": entrypoint,
            }
        elif projection_kind == "workflow":
            payload["workflow"] = {
                "execution_id": execution.execution_id,
                "revision_id": execution.revision_id,
                "entrypoint": entrypoint,
                "status": execution.status.value,
            }
        elif projection_kind == "ui":
            payload["ui"] = {
                "is_running": execution.status.value in _RUNNING_STATUSES,
                "label": entrypoint or "execution",
            }
        return payload

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.store.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> ExecutionProjectionRecord:
        value = json.loads(str(row["payload_json"]))
        if not isinstance(value, dict):
            raise ValueError("stored projection payload must be an object")
        return ExecutionProjectionRecord(
            projection_kind=str(row["projection_kind"]),
            event_sequence=int(row["event_sequence"]),
            execution_id=str(row["execution_id"]),
            session_id=str(row["session_id"]),
            status=str(row["status"]),
            payload=value,
            created_at=float(row["created_at"] if "created_at" in row.keys() else row["updated_at"]),
        )

    @staticmethod
    def _validate_kind(projection_kind: str) -> None:
        if projection_kind not in PROJECTION_KINDS:
            raise ValueError(f"unsupported projection kind: {projection_kind}")


def projection_handlers(store) -> dict[str, object]:
    """Return the fixed startup handlers; no caller-defined projection kinds."""
    model = ExecutionProjectionReadModel(store)
    return {
        kind: (lambda item, expected_kind=kind: model.apply(item, expected_kind=expected_kind))
        for kind in PROJECTION_KINDS
    }


def list_running_execution_projections() -> list[ExecutionProjectionRecord]:
    """Adapter used by the existing read-only Running surface."""
    from .store import default_store

    return ExecutionProjectionReadModel(default_store()).list_running()
