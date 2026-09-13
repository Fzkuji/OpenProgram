"""Execution persistence: store."""
from __future__ import annotations

import hashlib

import json


import sqlite3

import time

import uuid


from contextlib import closing, contextmanager


from pathlib import Path

from typing import Any, Iterator, Mapping


from .._schema import UnsupportedSchema, initialize_schema

from ..model import CapabilitySet, ExecutionInputRecord, ExecutionRecord, ExecutionStatus, RevisionRecord, RunRecord, TERMINAL_EXECUTION_STATUSES, _json, _snapshot_json


from .shared import (
    ExecutionConflict,
    ExecutionStoreError,
    _FINISH_REPAIR_HIGH_WATERMARK,
    _fingerprint,
    _log,
    _projection_event_written,
    _validate_agent_turn_payload,
    _validate_job_agent_payload,
)
from .state_blobs import StateBlobsOperations
from .finish_repair import FinishRepairOperations
from .projections import ProjectionsOperations
from .commands import CommandsOperations
from .events import EventsOperations
from .records import RecordsOperations

class ExecutionStore(StateBlobsOperations, FinishRepairOperations, ProjectionsOperations, CommandsOperations, EventsOperations, RecordsOperations):
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
    def _admission_transaction(self, session_id: str) -> Iterator[sqlite3.Connection]:
        """Serialize new execution admission with session migration holds."""
        from openprogram.paths import get_state_dir
        from openprogram.store.session.migration import session_hold_active
        from openprogram.store.session.session_lock import session_interprocess_lock

        with session_interprocess_lock(session_id, timeout=5.0):
            if session_hold_active(Path(get_state_dir()) / "sessions", session_id):
                raise ExecutionConflict(
                    "session_migration",
                    "execution admission is paused while session migration runs",
                )
            with self._transaction() as connection:
                yield connection


    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        event_token = _projection_event_written.set(False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
            if _projection_event_written.get():
                try:
                    from ..projections import wake_projection_worker

                    wake_projection_worker(self.path)
                except Exception:
                    # The outbox is durable; a missed in-process wake is
                    # recovered by the startup scan or worker idle poll.
                    _log.debug("projection worker wake failed", exc_info=True)
        except BaseException:
            connection.rollback()
            raise
        finally:
            _projection_event_written.reset(event_token)
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
        with self._admission_transaction(session_id) as connection:
            return self._create_execution_in_transaction(
                connection,
                session_id=session_id,
                revision_id=revision_id,
                run_id=run_id,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                source_checkpoint_id=source_checkpoint_id,
                capabilities=capabilities,
                emit_created_event=True,
            )


    def _create_execution_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        revision_id: str,
        run_id: str | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        source_checkpoint_id: str | None = None,
        capabilities: CapabilitySet = CapabilitySet(),
        emit_created_event: bool = True,
    ) -> ExecutionRecord:
        execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
        if not session_id or not revision_id:
            raise ExecutionConflict(
                "invalid_identity", "session_id and revision_id are required"
            )
        if parent_execution_id is None and source_checkpoint_id is not None:
            raise ExecutionConflict(
                "invalid_checkpoint",
                "root execution cannot have a source checkpoint",
            )
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
        if emit_created_event:
            self._append_event(
                connection,
                execution_id=execution_id,
                execution_version=record.status_version,
                kind="execution.created",
                payload={"record": record.to_dict()},
                created_at=now,
            )
        return record


    def admit_execution(
        self,
        *,
        session_id: str,
        revision_id: str,
        input_ref: str,
        input_hash: str,
        entrypoint: str,
        trusted_actor: Mapping[str, Any],
        config_snapshot_ref: str,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        run_id: str | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        source_checkpoint_id: str | None = None,
        capabilities: CapabilitySet = CapabilitySet(),
        agent_turn_payload: Mapping[str, Any] | None = None,
        job_agent_payload: Mapping[str, Any] | None = None,
        track_process_owner: bool = False,
    ) -> ExecutionRecord:
        """Admit one execution and its immutable input in one transaction."""
        if not input_ref or not input_hash or not entrypoint or not config_snapshot_ref:
            raise ExecutionConflict(
                "invalid_input", "input_ref, input_hash, entrypoint and config_snapshot_ref are required"
            )
        if not isinstance(trusted_actor, Mapping):
            raise ExecutionConflict("invalid_actor", "trusted_actor must be an object")
        actor_snapshot = _snapshot_json(trusted_actor)
        if agent_turn_payload is not None and job_agent_payload is not None:
            raise ExecutionConflict("invalid_input", "execution input has two Agent payloads")
        if agent_turn_payload is not None and not isinstance(agent_turn_payload, Mapping):
            raise ExecutionConflict("invalid_agent_input", "Agent turn input must be an object")
        if agent_turn_payload is not None:
            _validate_agent_turn_payload(agent_turn_payload)
        if job_agent_payload is not None and not isinstance(job_agent_payload, Mapping):
            raise ExecutionConflict("invalid_job_agent_input", "Job Agent input must be an object")
        if job_agent_payload is not None:
            _validate_job_agent_payload(job_agent_payload)
        agent_payload_json = (
            _json(dict(agent_turn_payload)) if agent_turn_payload is not None else None
        )
        job_payload_json = (
            _json(dict(job_agent_payload)) if job_agent_payload is not None else None
        )
        durable_payload_json = agent_payload_json or job_payload_json
        if job_payload_json is not None and hashlib.sha256(job_payload_json.encode("utf-8")).hexdigest() != input_hash:
            raise ExecutionConflict("input_hash_mismatch", "durable Job Agent input does not match input_hash")
        with self._admission_transaction(session_id) as connection:
            if durable_payload_json is not None:
                connection.execute(
                    "DELETE FROM execution_finish_repair_slots "
                    "WHERE execution_id NOT IN (SELECT execution_id FROM executions) "
                    "OR execution_id IN ("
                    "SELECT execution_id FROM executions WHERE status IN (?, ?, ?, ?)"
                    ")",
                    tuple(status.value for status in TERMINAL_EXECUTION_STATUSES),
                )
                reserved_slots = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM execution_finish_repair_slots"
                    ).fetchone()[0]
                )
                if reserved_slots >= _FINISH_REPAIR_HIGH_WATERMARK:
                    raise ExecutionConflict(
                        "finish_repair_capacity",
                        "Agent admission is paused while finish-repair slots drain",
                    )
            record = self._create_execution_in_transaction(
                connection,
                session_id=session_id,
                revision_id=revision_id,
                run_id=run_id,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                source_checkpoint_id=source_checkpoint_id,
                capabilities=capabilities,
                emit_created_event=False,
            )
            if track_process_owner:
                from ..process_owner import current_process_owner
                connection.execute(
                    "UPDATE executions SET owner_lease_json = ? WHERE execution_id = ?",
                    (_json({"process_owner": current_process_owner()}), record.execution_id),
                )
                record = self._require_execution(connection, record.execution_id)
            try:
                self._insert_execution_input(
                    connection,
                    ExecutionInputRecord(
                        execution_id=record.execution_id,
                        input_ref=input_ref,
                        input_hash=input_hash,
                        entrypoint=entrypoint,
                        session_id=session_id,
                        user_message_id=user_message_id,
                        assistant_message_id=assistant_message_id,
                        trusted_actor=actor_snapshot,
                        config_snapshot_ref=config_snapshot_ref,
                        created_at=record.created_at,
                    ),
                )
                if durable_payload_json is not None:
                    connection.execute(
                        "INSERT INTO execution_agent_turn_inputs "
                        "(execution_id, payload_json, content_hash, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            record.execution_id,
                            durable_payload_json,
                            hashlib.sha256(durable_payload_json.encode("utf-8")).hexdigest(),
                            record.created_at,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO execution_finish_repair_slots "
                        "(execution_id, reserved_at, updated_at, state) "
                        "VALUES (?, ?, ?, 'reserved')",
                        (record.execution_id, record.created_at, record.created_at),
                    )
            except sqlite3.IntegrityError as exc:
                raise ExecutionConflict(
                    "execution_input_exists", f"input already exists: {record.execution_id}"
                ) from exc
            self._append_event(
                connection,
                execution_id=record.execution_id,
                execution_version=record.status_version,
                kind="execution.created",
                payload={"record": record.to_dict()},
                created_at=record.created_at,
            )
        return record


    def get_execution_input(self, execution_id: str) -> ExecutionInputRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_inputs WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return self._execution_input(row) if row is not None else None


    def get_agent_turn_input(self, execution_id: str) -> Mapping[str, Any] | None:
        payload = self._get_durable_agent_input(execution_id)
        if payload is None or payload.get("kind") == "job_agent":
            return None
        _validate_agent_turn_payload(payload)
        return _snapshot_json(payload)


    def get_job_agent_input(self, execution_id: str) -> Mapping[str, Any] | None:
        payload = self._get_durable_agent_input(execution_id)
        if payload is None or payload.get("kind") != "job_agent":
            return None
        _validate_job_agent_payload(payload)
        return _snapshot_json(payload)


    def _get_durable_agent_input(self, execution_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json, content_hash FROM execution_agent_turn_inputs WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        payload_raw = str(row["payload_json"])
        if hashlib.sha256(payload_raw.encode("utf-8")).hexdigest() != str(row["content_hash"]):
            raise ExecutionConflict("agent_input_hash_mismatch", "stored Agent turn input failed integrity validation")
        payload = json.loads(payload_raw)
        if not isinstance(payload, dict):
            raise ExecutionConflict("invalid_agent_input", "stored Agent turn input must be an object")
        return payload


    def _copy_execution_input_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        source_execution_id: str,
        child_execution_id: str,
        created_at: float,
        assistant_message_id: str | None = None,
    ) -> ExecutionInputRecord:
        row = connection.execute(
            "SELECT * FROM execution_inputs WHERE execution_id = ?",
            (source_execution_id,),
        ).fetchone()
        if row is None:
            raise ExecutionConflict(
                "execution_input_missing",
                "branch source must have an immutable execution input",
            )
        source = self._execution_input(row)
        child = ExecutionInputRecord(
            execution_id=child_execution_id,
            input_ref=source.input_ref,
            input_hash=source.input_hash,
            entrypoint=source.entrypoint,
            session_id=source.session_id,
            user_message_id=source.user_message_id,
            assistant_message_id=assistant_message_id or source.assistant_message_id,
            trusted_actor=source.trusted_actor,
            config_snapshot_ref=source.config_snapshot_ref,
            created_at=created_at,
        )
        self._insert_execution_input(connection, child)
        payload = connection.execute(
            "SELECT payload_json, content_hash FROM execution_agent_turn_inputs WHERE execution_id = ?",
            (source_execution_id,),
        ).fetchone()
        if payload is not None:
            if hashlib.sha256(payload["payload_json"].encode("utf-8")).hexdigest() != payload["content_hash"]:
                raise ExecutionConflict("agent_input_hash_mismatch", "branch source Agent input failed integrity validation")
            connection.execute(
                "INSERT INTO execution_agent_turn_inputs (execution_id, payload_json, content_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (child_execution_id, payload["payload_json"], payload["content_hash"], created_at),
            )
        return child


    @staticmethod
    def _insert_execution_input(
        connection: sqlite3.Connection, record: ExecutionInputRecord
    ) -> None:
        connection.execute(
            "INSERT INTO execution_inputs ("
            "execution_id, input_ref, input_hash, entrypoint, session_id, "
            "user_message_id, assistant_message_id, trusted_actor_json, "
            "config_snapshot_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.execution_id,
                record.input_ref,
                record.input_hash,
                record.entrypoint,
                record.session_id,
                record.user_message_id,
                record.assistant_message_id,
                _json(record.trusted_actor),
                record.config_snapshot_ref,
                record.created_at,
            ),
        )


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
        # Preserve and reuse pre-v3 rows whose identity was hashed from only
        # the manifest.  Their stored hash remains untouched.
        for row in connection.execute(
            "SELECT * FROM revisions WHERE parent_revision_id IS ?",
            (parent_revision_id,),
        ):
            legacy = self._revision(row)
            if legacy.manifest == manifest_value:
                if requested_id is not None and legacy.revision_id != requested_id:
                    raise ExecutionConflict(
                        "revision_id_collision",
                        "explicit revision_id conflicts with existing content: "
                        f"{requested_id}",
                    )
                return legacy
        by_content_row = connection.execute(
            "SELECT * FROM revisions WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if by_content_row is not None:
            by_content = self._revision(by_content_row)
            if by_content.parent_revision_id == parent_revision_id:
                if requested_id is not None and by_content.revision_id != requested_id:
                    raise ExecutionConflict(
                        "revision_id_collision",
                        "explicit revision_id conflicts with existing content: "
                        f"{requested_id}",
                    )
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


    def list_for_session(self, session_id: str) -> list[ExecutionRecord]:
        """Read a session's complete execution history, including terminal work."""
        with closing(self._connect()) as connection:
            return [self._record(row) for row in connection.execute(
                "SELECT * FROM executions WHERE session_id = ? "
                "ORDER BY created_at DESC, execution_id",
                (session_id,),
            )]


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

