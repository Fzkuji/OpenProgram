"""Execution persistence: store."""
from __future__ import annotations

import hashlib

import json

import time

import uuid


from contextlib import closing, contextmanager


from pathlib import Path

from typing import Any, Iterator, Mapping


from ..model import CapabilitySet, ExecutionInputRecord, ExecutionRecord, ExecutionStatus, RevisionRecord, RunRecord, TERMINAL_EXECUTION_STATUSES, _json, _snapshot_json


from .shared import (
    ExecutionConflict,
    ExecutionStoreError,
    _fingerprint,
    _log,
    _event_written,
    _validate_agent_turn_payload,
    _validate_job_agent_payload,
)
from .run_tables import RowConflict, RunLogFiles, RunTables, TableHandle, log_for_path, tables_for
from .state_blobs import StateBlobsOperations
from .projections import ProjectionsOperations
from .commands import CommandsOperations
from .events import EventsOperations
from .records import RecordsOperations

class ExecutionStore(StateBlobsOperations, ProjectionsOperations, CommandsOperations, EventsOperations, RecordsOperations):
    """Transactional store with append-only events and materialized records."""

    def __init__(self, path: str | Path, *, log: RunLogFiles | None = None):
        self.path = Path(path)
        self.tables = tables_for(log if log is not None else log_for_path(self.path))


    def fresh_view(self) -> "ExecutionStore":
        """A new reading of the logs, for a process another process's changes do not reach."""
        view = type(self).__new__(type(self))
        view.path = self.path
        view.tables = RunTables(self.tables.log)
        return view


    def _connect(self) -> TableHandle:
        return self.tables.handle


    def _first_unresolved_effect(self, connection, execution_id: str) -> str | None:
        """An effect of this execution whose outcome is still unknown."""
        row = connection.first("effects", execution_id=execution_id, status=("dispatched", "uncertain"))
        if row is not None:
            return row["effect_id"]
        log = self.function_log(execution_id)
        steps = log.unresolved() if log is not None else ()
        return f"function-step:{execution_id}:{steps[0].action_id}" if steps else None


    def function_log_path(self, session_id: str, execution_id: str) -> Path:
        from ..function_log import function_log_name

        return self.tables.log.function_log_directory(session_id) / function_log_name(execution_id)


    def function_log(self, execution_id: str):
        """The execution's durable function record, or None when it is unknown."""
        from ..function_log import function_log_at

        record = self.get_execution(execution_id)
        return None if record is None else function_log_at(
            self.function_log_path(record.session_id, execution_id)
        )


    @contextmanager
    def _admission_transaction(self, session_id: str) -> Iterator[TableHandle]:
        """Serialize new execution admission with session migration holds.

        A rewind that changes the conversation's files holds them for the
        whole conversation (``operations.workspace_hold``); a new execution
        (a scheduled run, a queued Job) waits for it to end instead of
        starting on files that are being restored."""
        self.tables.ensure_session(session_id)
        from openprogram.state.paths import get_state_dir
        from openprogram.state.store.session.migration import session_hold_active
        from openprogram.state.store.session.operations import wait_for_workspace
        from openprogram.state.store.session.session_lock import session_interprocess_lock

        if not wait_for_workspace(session_id):
            raise ExecutionConflict(
                "workspace_rewind",
                "execution admission waits while a rewind restores this conversation's files",
            )
        with session_interprocess_lock(session_id, timeout=5.0):
            if session_hold_active(Path(get_state_dir()) / "projects", session_id):
                raise ExecutionConflict(
                    "session_migration",
                    "execution admission is paused while session migration runs",
                )
            with self._transaction() as connection:
                yield connection


    @contextmanager
    def _transaction(self) -> Iterator[TableHandle]:
        event_token = _event_written.set(False)
        try:
            with self.tables.transaction() as connection:
                yield connection
            if _event_written.get():
                from ..event_effects import wake_event_effects

                wake_event_effects(self.path)
        finally:
            _event_written.reset(event_token)


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
        connection: TableHandle,
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
                checkpoint = connection.get("checkpoints", source_checkpoint_id)
                if checkpoint is None or checkpoint["execution_id"] != parent_execution_id:
                    raise ExecutionConflict(
                        "invalid_checkpoint",
                        "source checkpoint does not belong to the parent execution",
                    )
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        now = time.time()
        run = self._get_run(connection, run_id)
        if run is None:
            connection.insert("runs", {"run_id": run_id, "session_id": session_id, "created_at": now})
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
        except RowConflict as exc:
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


    @staticmethod
    def admission_execution_id(session_id: str, key: str) -> str:
        return "exec_" + hashlib.sha256(_json([session_id, key]).encode()).hexdigest()

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
        admission_key: str | None = None,
        recovery_from: tuple[str, int] | None = None,
        client_request_id: str | None = None,
    ) -> ExecutionRecord:
        """Admit one execution and its immutable input in one transaction.

        ``client_request_id`` is the id the client gave the send; one send
        admits one execution per session. A stop that named the send before
        this admission (:meth:`stop_by_request`) is taken here, in the same
        transaction, and the turn ends as stopped when it activates."""
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
            if admission_key is not None:
                if not admission_key or execution_id is not None:
                    raise ExecutionConflict("invalid_admission_key", "Admission key requires a generated identity")
                execution_id = self.admission_execution_id(session_id, admission_key)
                existing = self._get_execution(connection, execution_id)
                if existing is not None:
                    saved = connection.get("execution_inputs", execution_id)
                    durable = connection.get("execution_agent_turn_inputs", execution_id)
                    if (saved is None or existing.session_id != session_id
                            or existing.parent_execution_id != parent_execution_id
                            or existing.source_checkpoint_id != source_checkpoint_id
                            or existing.capabilities != capabilities
                            or saved["input_hash"] != input_hash or saved["entrypoint"] != entrypoint
                            or json.loads(saved["trusted_actor_json"]) != actor_snapshot
                            or saved["config_snapshot_ref"] != config_snapshot_ref
                            or saved["user_message_id"] != user_message_id
                            or saved["assistant_message_id"] != assistant_message_id
                            or (durable["payload_json"] if durable else None) != durable_payload_json):
                        raise ExecutionConflict("admission_key_conflict", "Continuation key has different immutable input")
                    return existing
            if recovery_from is not None:
                from ..chat_recovery import recovery_state
                previous = self._require_execution(connection, recovery_from[0])
                if previous.session_id != session_id or previous.status_version != recovery_from[1]:
                    raise ExecutionConflict("stale_version", "Interrupted source changed before admission")
                state = recovery_state(self, previous)
                if not state["can_start_new_turn"]:
                    raise ExecutionConflict("recovery_unavailable", str(state["recovery_reason"]))
                if previous.status not in TERMINAL_EXECUTION_STATUSES:
                    self._transition_execution(connection, previous.execution_id,
                        expected_version=previous.status_version, target=ExecutionStatus.INTERRUPTED,
                        reason_code="restart_new_turn", clear_owner=True)
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
                connection.update(
                    "executions",
                    {"owner_lease_json": _json({"process_owner": current_process_owner()})},
                    execution_id=record.execution_id,
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
                    connection.insert("execution_agent_turn_inputs", {
                        "execution_id": record.execution_id,
                        "payload_json": durable_payload_json,
                        "content_hash": hashlib.sha256(durable_payload_json.encode("utf-8")).hexdigest(),
                        "created_at": record.created_at,
                    })
            except RowConflict as exc:
                raise ExecutionConflict(
                    "execution_input_exists", f"input already exists: {record.execution_id}"
                ) from exc
            if client_request_id:
                self._bind_client_request(
                    connection, session_id, client_request_id, record.execution_id, now=record.created_at,
                )
            self._append_event(
                connection,
                execution_id=record.execution_id,
                execution_version=record.status_version,
                kind="execution.created",
                payload={"record": record.to_dict()},
                created_at=record.created_at,
            )
        return record


    #: How long a stop that named a send waits for that send's admission. A
    #: send is admitted within moments of the stop on the same connection;
    #: the window covers a reconnect that delivers the stop first.
    STOP_MARK_SECONDS = 600.0


    def _bind_client_request(
        self, connection: TableHandle, session_id: str, client_request_id: str,
        execution_id: str, *, now: float,
    ) -> None:
        """Record the send's id on its admission and take a stop that named it."""
        try:
            connection.update(
                "execution_inputs", {"client_request_id": client_request_id},
                execution_id=execution_id,
            )
        except RowConflict as exc:
            raise ExecutionConflict(
                "request_admitted",
                f"this send was already admitted in session {session_id}",
            ) from exc
        mark = connection.get("execution_stop_marks", session_id, client_request_id)
        if mark is None or mark["execution_id"] is not None:
            return
        if mark["expires_at"] <= now:
            connection.delete("execution_stop_marks", session_id=session_id,
                              client_request_id=client_request_id)
            return
        connection.update(
            "execution_stop_marks", {"execution_id": execution_id},
            session_id=session_id, client_request_id=client_request_id,
        )


    def execution_for_request(self, session_id: str, client_request_id: str) -> str | None:
        """The execution a send admitted, or None before its admission."""
        self.tables.ensure_session(session_id)
        with closing(self._connect()) as connection:
            row = connection.first(
                "execution_inputs", session_id=session_id, client_request_id=client_request_id,
            )
            return row["execution_id"] if row is not None else None


    def stop_by_request(
        self,
        *,
        session_id: str,
        client_request_id: str,
        command_id: str,
        actor: Mapping[str, Any],
        reason_code: str,
        now: float | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Stop the turn a send starts, whether or not it was admitted yet.

        One transaction decides: when the send admitted an execution, its id
        comes back (``(execution_id, None)``) and the caller stops it like
        any other. Before that, a stop mark is written for the send, or the
        one already there is kept (``(None, mark)``); the send's admission
        takes it. A mark is written to the session's log, so it outlives a
        Worker restart, and lapses after :data:`STOP_MARK_SECONDS`.
        """
        if not session_id or not client_request_id or not command_id:
            raise ExecutionConflict("invalid_target", "a stop by request names its session and send")
        now = time.time() if now is None else now
        self.tables.ensure_session(session_id)
        with self._transaction() as connection:
            row = connection.first(
                "execution_inputs", session_id=session_id, client_request_id=client_request_id,
            )
            if row is not None:
                return row["execution_id"], None
            mark = connection.get("execution_stop_marks", session_id, client_request_id)
            if mark is not None and mark["expires_at"] > now:
                return None, mark
            connection.delete(
                "execution_stop_marks", session_id=session_id,
                where=lambda found: found["execution_id"] is None and found["expires_at"] <= now,
            )
            mark = connection.insert("execution_stop_marks", {
                "session_id": session_id,
                "client_request_id": client_request_id,
                "command_id": command_id,
                "actor_json": _json(_snapshot_json(actor)),
                "reason_code": reason_code,
                "created_at": now,
                "expires_at": now + self.STOP_MARK_SECONDS,
            }, on_conflict="replace")
            return None, mark


    def stop_mark_for(self, execution_id: str) -> dict[str, Any] | None:
        """The stop mark the admission of ``execution_id`` took, if any."""
        self.tables.ensure_execution(execution_id)
        with closing(self._connect()) as connection:
            return connection.first("execution_stop_marks", execution_id=execution_id)


    def drop_stop_mark(self, execution_id: str) -> None:
        """Forget a taken stop mark once its stop is written."""
        with self._transaction() as connection:
            connection.delete("execution_stop_marks", execution_id=execution_id)


    def get_execution_input(self, execution_id: str) -> ExecutionInputRecord | None:
        self.tables.ensure_execution(execution_id)
        with closing(self._connect()) as connection:
            row = connection.get("execution_inputs", execution_id)
            return self._execution_input(row) if row is not None else None


    def list_inputs_for_session(self, session_id: str) -> list[ExecutionInputRecord]:
        """The message anchors of every execution a session admitted."""
        self.tables.ensure_session(session_id)
        with closing(self._connect()) as connection:
            return [self._execution_input(row) for row in connection.rows("execution_inputs", session_id=session_id)]


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
        self.tables.ensure_execution(execution_id)
        with closing(self._connect()) as connection:
            row = connection.get("execution_agent_turn_inputs", execution_id)
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
        connection: TableHandle,
        *,
        source_execution_id: str,
        child_execution_id: str,
        created_at: float,
        assistant_message_id: str | None = None,
    ) -> ExecutionInputRecord:
        row = connection.get("execution_inputs", source_execution_id)
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
        payload = connection.get("execution_agent_turn_inputs", source_execution_id)
        if payload is not None:
            if hashlib.sha256(payload["payload_json"].encode("utf-8")).hexdigest() != payload["content_hash"]:
                raise ExecutionConflict("agent_input_hash_mismatch", "branch source Agent input failed integrity validation")
            connection.insert("execution_agent_turn_inputs", {
                "execution_id": child_execution_id,
                "payload_json": payload["payload_json"],
                "content_hash": payload["content_hash"],
                "created_at": created_at,
            })
        return child


    @staticmethod
    def _insert_execution_input(
        connection: TableHandle, record: ExecutionInputRecord
    ) -> None:
        connection.insert("execution_inputs", {
            "execution_id": record.execution_id,
            "input_ref": record.input_ref,
            "input_hash": record.input_hash,
            "entrypoint": record.entrypoint,
            "session_id": record.session_id,
            "user_message_id": record.user_message_id,
            "assistant_message_id": record.assistant_message_id,
            "trusted_actor_json": _json(record.trusted_actor),
            "config_snapshot_ref": record.config_snapshot_ref,
            "created_at": record.created_at,
        })


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
        connection: TableHandle,
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
        for row in connection.rows("revisions", parent_revision_id=parent_revision_id):
            legacy = self._revision(row)
            if legacy.manifest == manifest_value:
                if requested_id is not None and legacy.revision_id != requested_id:
                    raise ExecutionConflict(
                        "revision_id_collision",
                        "explicit revision_id conflicts with existing content: "
                        f"{requested_id}",
                    )
                return legacy
        by_content_row = connection.first("revisions", content_hash=content_hash)
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
        connection.insert("revisions", {
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "content_hash": content_hash,
            "manifest_json": _json(manifest_value),
            "created_at": now,
        })
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
        self.tables.ensure_session(session_id)
        with closing(self._connect()) as connection:
            return [self._record(row) for row in connection.rows(
                "executions", session_id=session_id, order=("-created_at", "execution_id"),
            )]


    def list_nonterminal(
        self, *, session_id: str | None = None
    ) -> list[ExecutionRecord]:
        terminal = {status.value for status in TERMINAL_EXECUTION_STATUSES}
        filters = {} if session_id is None else {"session_id": session_id}
        with closing(self._connect()) as connection:
            return [self._record(row) for row in connection.rows(
                "executions",
                where=lambda row: row["status"] not in terminal,
                order=("created_at", "execution_id"),
                **filters,
            )]


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
