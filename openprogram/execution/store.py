"""SQLite authority for canonical execution and control-command records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Collection, Iterator, Mapping

from ._schema import SCHEMA_VERSION, UnsupportedSchema, initialize_schema
from .model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionEvent,
    ExecutionRecord,
    ExecutionStatus,
    RevisionRecord,
    RunRecord,
    TERMINAL_COMMAND_STATUSES,
    TERMINAL_EXECUTION_STATUSES,
    _json,
    _snapshot_json,
)
from .state_machine import validate_command, validate_transition


class ExecutionStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ExecutionConflict(ExecutionStoreError):
    pass


class CommandConflict(ExecutionStoreError):
    pass


def _object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored JSON value must be an object")
    return value


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ExecutionStore:
    """Transactional store with append-only events and materialized records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            try:
                initialize_schema(connection)
            except UnsupportedSchema as exc:
                raise ExecutionStoreError(
                    "unsupported_schema",
                    f"execution store schema {exc.version} is not supported",
                ) from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_execution(
        self,
        *,
        session_id: str,
        revision_id: str,
        run_id: str | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        source_checkpoint_id: str | None = None,
        capabilities: CapabilitySet = CapabilitySet(),
    ) -> ExecutionRecord:
        execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
        if not session_id or not revision_id:
            raise ExecutionConflict(
                "invalid_identity", "session_id and revision_id are required"
            )
        with self._transaction() as connection:
            if self._get_revision(connection, revision_id) is None:
                raise ExecutionConflict(
                    "revision_not_found", f"revision not found: {revision_id}"
                )
            if parent_execution_id is not None:
                parent = self._get_execution(connection, parent_execution_id)
                if parent is None:
                    raise ExecutionConflict(
                        "parent_not_found", "parent execution does not exist"
                    )
                if run_id is None:
                    run_id = parent.run_id
                if parent.run_id != run_id or parent.session_id != session_id:
                    raise ExecutionConflict(
                        "parent_identity_mismatch",
                        "child execution must share its parent's run and session",
                    )
                if source_checkpoint_id is not None:
                    checkpoint = connection.execute(
                        "SELECT execution_id FROM checkpoints WHERE checkpoint_id = ?",
                        (source_checkpoint_id,),
                    ).fetchone()
                    if checkpoint is None or checkpoint["execution_id"] != parent_execution_id:
                        raise ExecutionConflict(
                            "invalid_checkpoint",
                            "source checkpoint does not belong to the parent execution",
                        )
            run_id = run_id or f"run_{uuid.uuid4().hex}"
            now = time.time()
            run = self._get_run(connection, run_id)
            if run is None:
                connection.execute(
                    "INSERT INTO runs (run_id, session_id, created_at) VALUES (?, ?, ?)",
                    (run_id, session_id, now),
                )
            elif run.session_id != session_id:
                raise ExecutionConflict(
                    "run_identity_mismatch",
                    "run_id is already bound to a different session",
                )
            record = ExecutionRecord(
                execution_id=execution_id,
                run_id=run_id,
                session_id=session_id,
                revision_id=revision_id,
                parent_execution_id=parent_execution_id,
                source_checkpoint_id=source_checkpoint_id,
                status=ExecutionStatus.QUEUED,
                status_version=1,
                capabilities=capabilities,
                created_at=now,
                updated_at=now,
            )
            try:
                self._insert_execution(connection, record)
            except sqlite3.IntegrityError as exc:
                raise ExecutionConflict(
                    "execution_exists", f"execution already exists: {execution_id}"
                ) from exc
            self._append_event(
                connection,
                execution_id=execution_id,
                execution_version=record.status_version,
                kind="execution.created",
                payload={"record": record.to_dict()},
                created_at=now,
            )
        return record

    def create_revision(
        self,
        *,
        manifest: Mapping[str, Any],
        revision_id: str | None = None,
        parent_revision_id: str | None = None,
    ) -> RevisionRecord:
        manifest_value = _snapshot_json(manifest)
        content_hash = _fingerprint(
            {"parent_revision_id": parent_revision_id, "manifest": manifest_value}
        )
        requested_id = revision_id
        revision_id = revision_id or f"rev_{content_hash[:32]}"
        with self._transaction() as connection:
            return self._create_revision_in_transaction(
                connection,
                manifest=manifest_value,
                revision_id=revision_id,
                parent_revision_id=parent_revision_id,
                requested_id=requested_id,
                content_hash=content_hash,
            )

    def _create_revision_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        manifest: Mapping[str, Any],
        revision_id: str | None,
        parent_revision_id: str | None,
        requested_id: str | None = None,
        content_hash: str | None = None,
    ) -> RevisionRecord:
        manifest_value = _snapshot_json(manifest)
        content_hash = content_hash or _fingerprint(
            {"parent_revision_id": parent_revision_id, "manifest": manifest_value}
        )
        revision_id = revision_id or f"rev_{content_hash[:32]}"
        existing = self._get_revision(connection, revision_id)
        if existing is not None:
            if (
                existing.content_hash != content_hash
                or existing.parent_revision_id != parent_revision_id
            ):
                raise ExecutionConflict(
                    "revision_id_collision",
                    f"revision_id already names different content: {revision_id}",
                )
            return existing
        by_content_row = connection.execute(
            "SELECT * FROM revisions WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if by_content_row is not None:
            by_content = self._revision(by_content_row)
            if by_content.parent_revision_id == parent_revision_id:
                return by_content
            raise ExecutionConflict(
                "revision_content_exists",
                f"revision content already exists as {by_content.revision_id}",
            )
        if (
            parent_revision_id is not None
            and self._get_revision(connection, parent_revision_id) is None
        ):
            raise ExecutionConflict(
                "parent_revision_not_found",
                f"parent revision not found: {parent_revision_id}",
            )
        now = time.time()
        connection.execute(
            "INSERT INTO revisions "
            "(revision_id, parent_revision_id, content_hash, manifest_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (revision_id, parent_revision_id, content_hash, _json(manifest_value), now),
        )
        return RevisionRecord(
            revision_id=revision_id,
            parent_revision_id=parent_revision_id,
            content_hash=content_hash,
            manifest=manifest_value,
            created_at=now,
        )

    def get_revision(self, revision_id: str) -> RevisionRecord | None:
        with closing(self._connect()) as connection:
            return self._get_revision(connection, revision_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        with closing(self._connect()) as connection:
            return self._get_run(connection, run_id)

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        with closing(self._connect()) as connection:
            return self._get_execution(connection, execution_id)

    def list_nonterminal(
        self, *, session_id: str | None = None
    ) -> list[ExecutionRecord]:
        terminal = tuple(status.value for status in TERMINAL_EXECUTION_STATUSES)
        placeholders = ",".join("?" for _ in terminal)
        query = f"SELECT * FROM executions WHERE status NOT IN ({placeholders})"
        values: list[Any] = list(terminal)
        if session_id is not None:
            query += " AND session_id = ?"
            values.append(session_id)
        query += " ORDER BY created_at, execution_id"
        with closing(self._connect()) as connection:
            return [self._record(row) for row in connection.execute(query, values)]

    def transition_execution(
        self,
        execution_id: str,
        *,
        expected_version: int,
        target: ExecutionStatus,
        reason_code: str | None = None,
    ) -> ExecutionRecord:
        with self._transaction() as connection:
            return self._transition_execution(
                connection,
                execution_id,
                expected_version=expected_version,
                target=target,
                reason_code=reason_code,
            )

    def accept_command(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
    ) -> ControlCommand:
        with self._transaction() as connection:
            command, _ = self._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload=payload,
                actor=actor,
            )
            return command

    def accept_command_with_transition(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        target: ExecutionStatus,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        reason_code: str | None = None,
        supersede_kinds: Collection[CommandKind] = (),
        supersede_code: str = "superseded",
        apply_command: bool = False,
    ) -> tuple[ControlCommand, ExecutionRecord, bool]:
        with self._transaction() as connection:
            command, duplicate = self._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload=payload,
                actor=actor,
            )
            if duplicate:
                execution = self._require_execution(connection, execution_id)
                return command, execution, True
            execution = self._transition_execution(
                connection,
                execution_id,
                expected_version=expected_version,
                target=target,
                reason_code=reason_code,
            )
            if supersede_kinds:
                values = tuple(kind.value for kind in supersede_kinds)
                placeholders = ",".join("?" for _ in values)
                rows = connection.execute(
                    "SELECT command_id, status FROM commands "
                    "WHERE execution_id = ? AND command_id != ? "
                    "AND status IN (?, ?) "
                    f"AND kind IN ({placeholders})",
                    (
                        execution_id,
                        command_id,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                        *values,
                    ),
                ).fetchall()
                for row in rows:
                    self._transition_command(
                        connection,
                        str(row["command_id"]),
                        expected_status=CommandStatus(row["status"]),
                        target=CommandStatus.REJECTED,
                        result_version=execution.status_version,
                        rejection_code=supersede_code,
                    )
            command = self._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
            if apply_command:
                command = self._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            return command, execution, False

    def get_command(self, command_id: str) -> ControlCommand | None:
        with closing(self._connect()) as connection:
            return self._get_command(connection, command_id)

    def list_commands(
        self,
        execution_id: str,
        *,
        statuses: Collection[CommandStatus] = (),
        kinds: Collection[CommandKind] = (),
    ) -> list[ControlCommand]:
        query = "SELECT * FROM commands WHERE execution_id = ?"
        values: list[Any] = [execution_id]
        if statuses:
            status_values = tuple(status.value for status in statuses)
            query += " AND status IN (" + ",".join("?" for _ in status_values) + ")"
            values.extend(status_values)
        if kinds:
            kind_values = tuple(kind.value for kind in kinds)
            query += " AND kind IN (" + ",".join("?" for _ in kind_values) + ")"
            values.extend(kind_values)
        query += " ORDER BY submitted_at, command_id"
        with closing(self._connect()) as connection:
            return [self._command(row) for row in connection.execute(query, values)]

    def transition_command(
        self,
        command_id: str,
        *,
        expected_status: CommandStatus,
        target: CommandStatus,
        result_version: int | None = None,
        rejection_code: str | None = None,
        receipt: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        with self._transaction() as connection:
            return self._transition_command(
                connection,
                command_id,
                expected_status=expected_status,
                target=target,
                result_version=result_version,
                rejection_code=rejection_code,
                receipt=receipt,
            )

    def list_events(self, execution_id: str) -> list[ExecutionEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM execution_events WHERE execution_id = ? "
                "ORDER BY sequence",
                (execution_id,),
            )
            return [
                ExecutionEvent(
                    sequence=int(row["sequence"]),
                    execution_id=str(row["execution_id"]),
                    execution_version=row["execution_version"],
                    command_id=row["command_id"],
                    kind=str(row["kind"]),
                    payload=_object(row["payload_json"]),
                    created_at=float(row["created_at"]),
                    schema_version=int(row["schema_version"]),
                )
                for row in rows
            ]

    def rebuild_execution(self, execution_id: str) -> ExecutionRecord | None:
        record = None
        for event in self.list_events(execution_id):
            if event.kind in {"execution.created", "execution.updated"}:
                record = ExecutionRecord.from_dict(event.payload["record"])
        return record

    def _accept_command(
        self,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
    ) -> tuple[ControlCommand, bool]:
        fingerprint = _fingerprint(
            {
                "execution_id": execution_id,
                "expected_version": expected_version,
                "kind": kind.value,
                "payload": dict(payload),
                "actor": dict(actor),
            }
        )
        existing_row = connection.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if existing_row is not None:
            if existing_row["fingerprint"] != fingerprint:
                raise CommandConflict(
                    "idempotency_collision",
                    f"command_id was already used for a different request: {command_id}",
                )
            return self._command(existing_row), True

        execution = self._require_execution(connection, execution_id)
        if execution.status_version != expected_version:
            raise ExecutionConflict(
                "stale_version",
                f"expected execution version {expected_version}, "
                f"found {execution.status_version}",
            )
        validate_command(kind, execution.status, execution.capabilities)
        now = time.time()
        command = ControlCommand(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=kind,
            payload=dict(payload),
            actor=dict(actor),
            status=CommandStatus.ACCEPTED,
            submitted_at=now,
            updated_at=now,
        )
        connection.execute(
            "INSERT INTO commands "
            "(command_id, execution_id, expected_version, kind, payload_json, "
            "actor_json, fingerprint, status, submitted_at, updated_at, "
            "result_version, rejection_code, result_json) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command.command_id,
                command.execution_id,
                command.expected_version,
                command.kind.value,
                _json(command.payload),
                _json(command.actor),
                fingerprint,
                command.status.value,
                command.submitted_at,
                command.updated_at,
                command.result_version,
                command.rejection_code,
                _json(command.result_json),
            ),
        )
        self._append_event(
            connection,
            execution_id=execution_id,
            execution_version=execution.status_version,
            command_id=command_id,
            kind="command.accepted",
            payload={"command": command.to_dict()},
            created_at=now,
        )
        return command, False

    def _transition_execution(
        self,
        connection: sqlite3.Connection,
        execution_id: str,
        *,
        expected_version: int,
        target: ExecutionStatus,
        reason_code: str | None,
        clear_owner: bool = False,
    ) -> ExecutionRecord:
        current = self._require_execution(connection, execution_id)
        if current.status_version != expected_version:
            raise ExecutionConflict(
                "stale_version",
                f"expected execution version {expected_version}, "
                f"found {current.status_version}",
            )
        if current.status in TERMINAL_EXECUTION_STATUSES:
            raise ExecutionConflict(
                "terminal", f"execution is already {current.status.value}"
            )
        if current.status is not target or not clear_owner:
            validate_transition(current.status, target)
        now = time.time()
        terminal_at = now if target in TERMINAL_EXECUTION_STATUSES else None
        new_version = expected_version + 1
        if clear_owner:
            updated = connection.execute(
                "UPDATE executions SET status = ?, status_version = ?, "
                "reason_code = ?, updated_at = ?, terminal_at = ?, "
                "current_attempt_id = NULL, owner_lease_json = '{}' "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    target.value,
                    new_version,
                    reason_code,
                    now,
                    terminal_at,
                    execution_id,
                    expected_version,
                ),
            )
        else:
            updated = connection.execute(
                "UPDATE executions SET status = ?, status_version = ?, "
                "reason_code = ?, updated_at = ?, terminal_at = ? "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    target.value,
                    new_version,
                    reason_code,
                    now,
                    terminal_at,
                    execution_id,
                    expected_version,
                ),
            )
        if updated.rowcount != 1:
            raise ExecutionConflict("stale_version", "execution changed concurrently")
        record = self._require_execution(connection, execution_id)
        self._append_event(
            connection,
            execution_id=execution_id,
            execution_version=record.status_version,
            kind="execution.updated",
            payload={"record": record.to_dict()},
            created_at=now,
        )
        return record

    def _transition_command(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        *,
        expected_status: CommandStatus,
        target: CommandStatus,
        result_version: int | None = None,
        rejection_code: str | None = None,
        receipt: Mapping[str, Any] | None = None,
        result_json: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        current = self._get_command(connection, command_id)
        if current is None:
            raise CommandConflict("not_found", f"command not found: {command_id}")
        if current.status in TERMINAL_COMMAND_STATUSES:
            raise CommandConflict(
                "terminal", f"command is already {current.status.value}"
            )
        if current.status is not expected_status:
            raise CommandConflict(
                "stale_status",
                f"expected command status {expected_status.value}, "
                f"found {current.status.value}",
            )
        allowed = {
            CommandStatus.ACCEPTED: {
                CommandStatus.APPLYING,
                CommandStatus.REJECTED,
            },
            CommandStatus.APPLYING: {
                CommandStatus.APPLIED,
                CommandStatus.REJECTED,
            },
        }
        if target not in allowed[current.status]:
            raise CommandConflict(
                "invalid_transition",
                f"invalid command transition: {current.status.value} -> {target.value}",
            )
        now = time.time()
        connection.execute(
            "UPDATE commands SET status = ?, updated_at = ?, "
            "result_version = ?, rejection_code = ?, result_json = ? WHERE command_id = ?",
            (
                target.value,
                now,
                result_version,
                rejection_code,
                _json(result_json or {}),
                command_id,
            ),
        )
        command = self._get_command(connection, command_id)
        assert command is not None
        payload: dict[str, Any] = {"command": command.to_dict()}
        if receipt is not None:
            payload["receipt"] = dict(receipt)
        if result_json is not None:
            payload["result"] = dict(result_json)
        self._append_event(
            connection,
            execution_id=command.execution_id,
            execution_version=result_version,
            command_id=command_id,
            kind=f"command.{target.value}",
            payload=payload,
            created_at=now,
        )
        return command

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
    ) -> None:
        connection.execute(
            "INSERT INTO execution_events "
            "(execution_id, execution_version, command_id, kind, payload_json, "
            "created_at, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                execution_id,
                execution_version,
                command_id,
                kind,
                _json(payload),
                created_at,
                SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _insert_execution(
        connection: sqlite3.Connection, record: ExecutionRecord
    ) -> None:
        connection.execute(
            "INSERT INTO executions "
            "(execution_id, run_id, session_id, parent_execution_id, source_checkpoint_id, "
            "revision_id, status, status_version, reason_code, "
            "current_attempt_id, owner_lease_json, checkpoint_head_id, "
            "safe_point_json, capabilities_json, effect_summary_json, "
            "created_at, updated_at, terminal_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.execution_id,
                record.run_id,
                record.session_id,
                record.parent_execution_id,
                record.source_checkpoint_id,
                record.revision_id,
                record.status.value,
                record.status_version,
                record.reason_code,
                record.current_attempt_id,
                _json(record.owner_lease),
                record.checkpoint_head_id,
                _json(record.safe_point),
                _json(record.capabilities.to_dict()),
                _json(record.effect_summary),
                record.created_at,
                record.updated_at,
                record.terminal_at,
            ),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ExecutionRecord:
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
    def _command(row: sqlite3.Row) -> ControlCommand:
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
        self, connection: sqlite3.Connection, execution_id: str
    ) -> ExecutionRecord | None:
        row = connection.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        return self._record(row) if row is not None else None

    def _require_execution(
        self, connection: sqlite3.Connection, execution_id: str
    ) -> ExecutionRecord:
        record = self._get_execution(connection, execution_id)
        if record is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        return record

    def _get_command(
        self, connection: sqlite3.Connection, command_id: str
    ) -> ControlCommand | None:
        row = connection.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        return self._command(row) if row is not None else None

    @staticmethod
    def _revision(row: sqlite3.Row) -> RevisionRecord:
        return RevisionRecord(
            revision_id=str(row["revision_id"]),
            parent_revision_id=row["parent_revision_id"],
            content_hash=str(row["content_hash"]),
            manifest=_object(row["manifest_json"]),
            created_at=float(row["created_at"]),
        )

    def _get_revision(
        self, connection: sqlite3.Connection, revision_id: str
    ) -> RevisionRecord | None:
        row = connection.execute(
            "SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        return self._revision(row) if row is not None else None

    @staticmethod
    def _get_run(connection: sqlite3.Connection, run_id: str) -> RunRecord | None:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            created_at=float(row["created_at"]),
        )


@lru_cache(maxsize=8)
def _store_for_path(path: Path) -> ExecutionStore:
    return ExecutionStore(path)


def default_store() -> ExecutionStore:
    """Return the store for the currently active profile."""
    from openprogram.paths import get_execution_db_path

    return _store_for_path(get_execution_db_path())
