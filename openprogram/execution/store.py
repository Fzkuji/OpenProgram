"""SQLite authority for canonical execution and control-command records."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextvars import ContextVar
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Collection, Iterator, Mapping

from ._schema import PROJECTION_KINDS, SCHEMA_VERSION, UnsupportedSchema, initialize_schema
from .model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionEvent,
    ExecutionInputRecord,
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


_log = logging.getLogger(__name__)


class ExecutionStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ExecutionConflict(ExecutionStoreError):
    pass


class CommandConflict(ExecutionStoreError):
    pass


class ProjectionConflict(ExecutionStoreError):
    pass


_AGENT_TURN_INPUT_VERSION = 1
_AGENT_TURN_INPUT_MAX_BYTES = 256 * 1024
_AGENT_TURN_INPUT_KINDS = frozenset({"chat", "forced_tool"})
_FINISH_REPAIR_HIGH_WATERMARK = 4096
_FINISH_REPAIR_PAGE_LIMIT = 4096
_AGENT_TURN_INPUT_KEYS = frozenset({
    "version", "kind", "request", "tool_name", "tool_input",
    "anchor_msg_id", "work_dir", "agent_id", "source", "provider", "model",
    "response_format", "surface_context_snapshot",
})
_STATE_REF_PREFIX = "execstate://sha256/"
_STATE_HASH_LENGTH = 64
MAX_AGENT_STATE_BLOB_BYTES = 1024 * 1024
RESOURCE_INTENT_KINDS = frozenset({
    "execution.admission.intent", "resource.admission.intent",
    "execution.claim.intent", "resource.claim.intent",
    "execution.release.intent", "resource.release.intent",
})


def _validate_agent_turn_payload(payload: Mapping[str, Any]) -> None:
    """Validate the durable Agent envelope without importing Agent runtime."""
    if not isinstance(payload, Mapping):
        raise ExecutionConflict("invalid_agent_input", "Agent turn input must be an object")
    if payload.get("version") != _AGENT_TURN_INPUT_VERSION:
        raise ExecutionConflict("invalid_agent_input_version", "unsupported Agent turn input version")
    kind = payload.get("kind")
    if kind not in _AGENT_TURN_INPUT_KINDS:
        raise ExecutionConflict("invalid_agent_input_kind", "Agent turn input kind must be chat or forced_tool")
    if set(payload) - _AGENT_TURN_INPUT_KEYS:
        raise ExecutionConflict("invalid_agent_input", "Agent turn input has unknown fields")
    if kind == "chat":
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise ExecutionConflict("invalid_agent_input", "chat input requires a request object")
        for required in ("user_text", "agent_id", "source"):
            if not isinstance(request.get(required), str) or not request[required]:
                raise ExecutionConflict("invalid_agent_input", f"chat input requires {required}")
    else:
        if not isinstance(payload.get("tool_name"), str) or not payload["tool_name"]:
            raise ExecutionConflict("invalid_agent_input", "forced_tool input requires tool_name")
        if not isinstance(payload.get("tool_input", {}), Mapping):
            raise ExecutionConflict("invalid_agent_input", "forced_tool input requires an object tool_input")
    try:
        encoded = _json(payload)
    except (TypeError, ValueError) as exc:
        raise ExecutionConflict("invalid_agent_input", "Agent turn input must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > _AGENT_TURN_INPUT_MAX_BYTES:
        raise ExecutionConflict("agent_input_too_large", "Agent turn input exceeds the size limit")


def _validate_job_agent_payload(payload: Mapping[str, Any]) -> None:
    try:
        from openprogram.agent.job.input import normalize_job_agent_input
        normalize_job_agent_input(payload)
    except ValueError as exc:
        raise ExecutionConflict("invalid_job_agent_input", str(exc)) from exc


def _object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stored JSON value must be an object")
    return value


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_projection_event_written: ContextVar[bool] = ContextVar(
    "execution_projection_event_written", default=False
)


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
        event_token = _projection_event_written.set(False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
            if _projection_event_written.get():
                try:
                    from .projections import wake_projection_worker

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
        with self._transaction() as connection:
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
        with self._transaction() as connection:
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

    def put_state_blob(
        self,
        execution_id: str,
        payload: bytes | str,
        *,
        media_type: str = "application/json",
        schema_version: int = 1,
    ) -> dict[str, Any]:
        """Persist one execution-owned immutable state blob."""
        with self._transaction() as connection:
            return self._put_state_blob_in_transaction(
                connection,
                execution_id=execution_id,
                payload=payload,
                media_type=media_type,
                schema_version=schema_version,
            )

    def get_state_blob(self, execution_id: str, ref: str) -> dict[str, Any] | None:
        self._validate_state_ref(ref)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT ref, sha256, payload, byte_length, media_type, schema_version "
                "FROM execution_state_blobs WHERE execution_id = ? AND ref = ?",
                (execution_id, ref),
            ).fetchone()
        if row is None:
            return None
        payload = bytes(row["payload"])
        digest = hashlib.sha256(payload).hexdigest()
        if digest != row["sha256"] or len(payload) != int(row["byte_length"]):
            raise ExecutionStoreError(
                "state_blob_corrupt", "stored state blob failed integrity validation"
            )
        return {
            "ref": str(row["ref"]), "sha256": digest,
            "byte_length": len(payload), "media_type": str(row["media_type"]),
            "schema_version": int(row["schema_version"]), "payload": payload,
        }

    def gc_state_blobs(self, execution_id: str) -> int:
        """Remove only terminal, unreferenced state owned by this execution."""
        with self._transaction() as connection:
            execution = self._require_execution(connection, execution_id)
            if execution.status not in TERMINAL_EXECUTION_STATUSES:
                raise ExecutionConflict("state_gc_not_terminal", "state blobs remain while execution is nonterminal")
            referenced: set[str] = set()
            for row in connection.execute(
                "SELECT state_refs_json, effect_receipts_json FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            ):
                for raw in (row["state_refs_json"], row["effect_receipts_json"]):
                    self._collect_state_refs(json.loads(raw), referenced)
            self._expand_state_blob_refs(connection, execution_id, referenced)
            for row in connection.execute(
                "SELECT receipt_json FROM effects WHERE execution_id = ?", (execution_id,),
            ):
                self._collect_state_refs(json.loads(row["receipt_json"]), referenced)
            self._expand_state_blob_refs(connection, execution_id, referenced)
            if referenced:
                placeholders = ",".join("?" for _ in referenced)
                result = connection.execute(
                    f"DELETE FROM execution_state_blobs WHERE execution_id = ? AND ref NOT IN ({placeholders})",
                    (execution_id, *sorted(referenced)),
                )
            else:
                result = connection.execute(
                    "DELETE FROM execution_state_blobs WHERE execution_id = ?", (execution_id,)
                )
            return int(result.rowcount)

    @classmethod
    def _collect_state_refs(cls, value: Any, refs: set[str]) -> None:
        # AgentCheckpointV1 uses bare refs for well-known durable fields;
        # effect receipts use ``receipt_ref``.  Treat every valid-looking
        # execstate string as a reference, regardless of surrounding shape.
        if isinstance(value, str) and value.startswith(_STATE_REF_PREFIX):
            cls._validate_state_ref(value)
            refs.add(value)
            return
        if isinstance(value, Mapping):
            ref = value.get("ref")
            if isinstance(ref, str) and ref.startswith(_STATE_REF_PREFIX):
                cls._validate_state_ref(ref)
                refs.add(ref)
            for item in value.values():
                cls._collect_state_refs(item, refs)
        elif isinstance(value, list):
            for item in value:
                cls._collect_state_refs(item, refs)

    @classmethod
    def _expand_state_blob_refs(
        cls, connection: sqlite3.Connection, execution_id: str, refs: set[str],
    ) -> None:
        """Expand the child refs of the one versioned Agent checkpoint schema.

        A checkpoint manifest stores the Agent payload as one immutable blob;
        that payload stores descriptors for message deltas, snapshots, and
        receipts.  GC must resolve that schema before deciding which blobs are
        unreachable.  Other JSON state blobs remain opaque by design.
        """
        pending = list(refs)
        visited: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref in visited:
                continue
            visited.add(ref)
            row = connection.execute(
                "SELECT payload, media_type, schema_version FROM execution_state_blobs "
                "WHERE execution_id = ? AND ref = ?",
                (execution_id, ref),
            ).fetchone()
            if row is None or row["media_type"] != "application/json" or int(row["schema_version"]) != 1:
                continue
            try:
                value = json.loads(bytes(row["payload"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                continue
            if not all(key in value for key in ("safe_point", "frontier", "turn", "current_decision", "state_refs")):
                continue
            before = len(refs)
            state_refs = value.get("state_refs")
            if isinstance(state_refs, Mapping):
                cls._collect_state_refs(state_refs, refs)
            if len(refs) > before:
                pending.extend(refs - visited)

    def get_agent_wait(self, execution_id: str, kind: str) -> None:
        """Agent P0 does not persist waits; this explicit query stays empty."""
        self.get_execution(execution_id)
        return None

    @staticmethod
    def _validate_state_ref(ref: str) -> None:
        if (
            not isinstance(ref, str)
            or not ref.startswith(_STATE_REF_PREFIX)
            or len(ref) != len(_STATE_REF_PREFIX) + _STATE_HASH_LENGTH
            or any(char not in "0123456789abcdef" for char in ref[len(_STATE_REF_PREFIX):])
        ):
            raise ExecutionConflict(
                "state_ref_invalid", "state ref must be an execstate sha256 reference"
            )

    def _put_state_blob_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        payload: bytes | str,
        media_type: str,
        schema_version: int,
    ) -> dict[str, Any]:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if not isinstance(payload, bytes):
            raise ExecutionConflict("state_ref_invalid", "state blob payload must be bytes or UTF-8 text")
        if len(payload) > MAX_AGENT_STATE_BLOB_BYTES:
            raise ExecutionConflict("state_blob_too_large", "Agent state blob exceeds the size limit")
        if not media_type or not isinstance(media_type, str) or type(schema_version) is not int or schema_version < 1:
            raise ExecutionConflict("state_ref_invalid", "state blob media type and schema version are required")
        self._require_execution(connection, execution_id)
        digest = hashlib.sha256(payload).hexdigest()
        ref = f"{_STATE_REF_PREFIX}{digest}"
        row = connection.execute(
            "SELECT sha256, payload, byte_length, media_type, schema_version "
            "FROM execution_state_blobs WHERE execution_id = ? AND ref = ?",
            (execution_id, ref),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO execution_state_blobs "
                "(execution_id, ref, sha256, payload, byte_length, media_type, schema_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (execution_id, ref, digest, payload, len(payload), media_type, schema_version, time.time()),
            )
        elif (
            row["sha256"] != digest or bytes(row["payload"]) != payload
            or int(row["byte_length"]) != len(payload)
            or row["media_type"] != media_type or int(row["schema_version"]) != schema_version
        ):
            raise ExecutionConflict("state_ref_invalid", "state blob reference collides with different content")
        return {
            "ref": ref, "sha256": digest, "byte_length": len(payload),
            "media_type": media_type, "schema_version": schema_version,
        }

    def upsert_finish_repair(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        expected_version: int,
        target: str,
        outcome: str,
        reason_code: str | None,
        command_id: str | None = None,
        retry_count: int = 0,
        next_attempt_at: float = 0.0,
    ) -> None:
        """Persist a terminal write for bounded retry and startup replay."""
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError("finish repair retry_count must be non-negative")
        if type(next_attempt_at) not in {int, float}:
            raise ValueError("finish repair next_attempt_at must be numeric")
        now = time.time()
        with self._transaction() as connection:
            # Only terminal or stale rows may be collected. An actionable
            # repair is retained even when the bounded table is full.
            rows = connection.execute(
                """
                SELECT r.execution_id, r.attempt_id, r.generation,
                       e.status AS execution_status,
                       e.current_attempt_id AS current_attempt_id,
                       e.owner_lease_json AS owner_lease_json,
                       a.execution_id AS attempt_execution_id,
                       a.generation AS attempt_generation,
                       a.status AS attempt_status
                FROM execution_finish_repairs AS r
                LEFT JOIN executions AS e ON e.execution_id = r.execution_id
                LEFT JOIN attempts AS a ON a.attempt_id = r.attempt_id
                """
            ).fetchall()
            terminal_values = {status.value for status in TERMINAL_EXECUTION_STATUSES}
            stale_keys = []
            for row in rows:
                try:
                    owner_lease = json.loads(row["owner_lease_json"] or "{}")
                except (TypeError, ValueError):
                    owner_lease = {}
                if (
                    row["execution_status"] is None
                    or row["execution_status"] in terminal_values
                    or row["current_attempt_id"] != row["attempt_id"]
                    or row["attempt_execution_id"] != row["execution_id"]
                    or row["attempt_generation"] != row["generation"]
                    or row["attempt_status"] != "active"
                    or owner_lease.get("generation") != row["generation"]
                ):
                    stale_keys.append(
                        (row["execution_id"], row["attempt_id"], row["generation"])
                    )
            for stale_key in stale_keys:
                connection.execute(
                    "DELETE FROM execution_finish_repairs "
                    "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                    stale_key,
                )
                execution_status = connection.execute(
                    "SELECT status FROM executions WHERE execution_id = ?",
                    (stale_key[0],),
                ).fetchone()
                if (
                    execution_status is None
                    or execution_status["status"] in terminal_values
                ):
                    connection.execute(
                        "DELETE FROM execution_finish_repair_slots WHERE execution_id = ?",
                        (stale_key[0],),
                    )
            connection.execute(
                """
                INSERT INTO execution_finish_repairs (
                    execution_id, attempt_id, generation, expected_version,
                    target, outcome, reason_code, created_at, updated_at,
                    command_id, retry_count, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(execution_id, attempt_id, generation) DO UPDATE SET
                    expected_version = excluded.expected_version,
                    target = excluded.target,
                    outcome = excluded.outcome,
                    reason_code = excluded.reason_code,
                    command_id = excluded.command_id,
                    retry_count = excluded.retry_count,
                    next_attempt_at = excluded.next_attempt_at,
                    updated_at = excluded.updated_at
                """,
                (
                    execution_id, attempt_id, generation, expected_version,
                    target, outcome, reason_code, now, now, command_id,
                    retry_count, next_attempt_at,
                ),
            )
            connection.execute(
                "UPDATE execution_finish_repair_slots SET state = 'repair', "
                "updated_at = ? WHERE execution_id = ?",
                (now, execution_id),
            )

    def defer_finish_repair(
        self,
        execution_id: str,
        attempt_id: str,
        generation: int,
        *,
        retry_count: int,
        next_attempt_at: float,
    ) -> None:
        """Persist repair backoff without changing its desired outcome."""
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError("finish repair retry_count must be non-negative")
        if type(next_attempt_at) not in {int, float}:
            raise ValueError("finish repair next_attempt_at must be numeric")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE execution_finish_repairs SET retry_count = ?, "
                "next_attempt_at = ?, updated_at = ? "
                "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                (
                    retry_count, next_attempt_at, time.time(),
                    execution_id, attempt_id, generation,
                ),
            )

    def list_finish_repairs(
        self, *, limit: int = 256, offset: int = 0,
        include_stalled: bool = True,
        after: tuple[float, str, str, int] | None = None,
        due_before: float | None = None,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 0 < limit <= _FINISH_REPAIR_PAGE_LIMIT:
            raise ValueError("finish repair limit is out of bounds")
        if type(offset) is not int or offset < 0:
            raise ValueError("finish repair offset must be non-negative")
        if type(include_stalled) is not bool:
            raise ValueError("include_stalled must be a bool")
        if due_before is not None and type(due_before) not in {int, float}:
            raise ValueError("finish repair due_before must be numeric")
        if after is not None and (
            type(after) is not tuple
            or len(after) != 4
            or type(after[0]) not in {int, float}
            or not all(type(value) is str for value in after[1:3])
            or type(after[3]) is not int
        ):
            raise ValueError("finish repair cursor is invalid")
        with closing(self._connect()) as connection:
            where = "(? OR reason_code IS NULL OR reason_code != ?)"
            values: list[Any] = [include_stalled, "finish_repair_stalled"]
            if due_before is not None:
                where += " AND next_attempt_at <= ?"
                values.append(due_before)
            if after is not None:
                where += (
                    " AND (created_at > ? OR "
                    "(created_at = ? AND execution_id > ?) OR "
                    "(created_at = ? AND execution_id = ? AND attempt_id > ?) OR "
                    "(created_at = ? AND execution_id = ? AND attempt_id = ? "
                    "AND generation > ?))"
                )
                created_at, execution_id, attempt_id, generation = after
                values.extend(
                    [
                        created_at,
                        created_at,
                        execution_id,
                        created_at,
                        execution_id,
                        attempt_id,
                        created_at,
                        execution_id,
                        attempt_id,
                        generation,
                    ]
                )
            values.extend([limit, offset])
            rows = connection.execute(
                "SELECT * FROM execution_finish_repairs "
                f"WHERE {where} "
                "ORDER BY created_at, execution_id, attempt_id, generation "
                "LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_finish_repair_slots(self, *, limit: int = 4096) -> list[dict[str, Any]]:
        if type(limit) is not int or not 0 < limit <= _FINISH_REPAIR_PAGE_LIMIT:
            raise ValueError("finish repair slot limit is out of bounds")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM execution_finish_repair_slots "
                "ORDER BY reserved_at, execution_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_stalled_finish_repairs(self) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM execution_finish_repairs "
                "WHERE reason_code = 'finish_repair_stalled' LIMIT 1"
            ).fetchone()
        return row is not None

    def delete_finish_repair(
        self, execution_id: str, attempt_id: str, generation: int,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM execution_finish_repairs "
                "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                (execution_id, attempt_id, generation),
            )
            # Removing one stale/handled generation does not release the
            # execution's admission reservation. If no repair remains for a
            # live execution, expose the reservation as available for a new
            # repair rather than leaving it marked as an old repair.
            connection.execute(
                "UPDATE execution_finish_repair_slots SET state = 'reserved', "
                "updated_at = ? WHERE execution_id = ? "
                "AND NOT EXISTS ("
                "SELECT 1 FROM execution_finish_repairs "
                "WHERE execution_id = ?"
                ") AND EXISTS ("
                "SELECT 1 FROM executions WHERE execution_id = ? "
                "AND status NOT IN (?, ?, ?, ?)"
                ")",
                (
                    time.time(), execution_id, execution_id, execution_id,
                    *(status.value for status in TERMINAL_EXECUTION_STATUSES),
                ),
            )

    def _copy_execution_input_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        source_execution_id: str,
        child_execution_id: str,
        created_at: float,
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
            assistant_message_id=source.assistant_message_id,
            trusted_actor=source.trusted_actor,
            config_snapshot_ref=source.config_snapshot_ref,
            created_at=created_at,
        )
        self._insert_execution_input(connection, child)
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

    def get_projection_outbox(self, outbox_id: str):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            return self._projection_outbox(row) if row is not None else None

    def enqueue_resource_intent(
        self,
        execution_id: str,
        *,
        kind: str,
        idempotency_key: str,
        fingerprint: str,
        admission_id: str | None = None,
        attempt_id: str | None = None,
        generation: int | None = None,
        resource_lease_generation: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one execution-owned cross-authority intent.

        This transaction deliberately does not call ResourceGovernor.  The
        resulting row is the durable hand-off between the two SQLite files.
        """
        if kind not in RESOURCE_INTENT_KINDS:
            raise ExecutionConflict("invalid_resource_intent", f"unsupported resource intent: {kind}")
        if not idempotency_key or not fingerprint:
            raise ExecutionConflict("invalid_resource_intent", "idempotency_key and fingerprint are required")
        encoded = _json(dict(payload or {}))
        now = time.time()
        with self._transaction() as connection:
            execution = self._require_execution(connection, execution_id)
            existing = connection.execute(
                "SELECT * FROM execution_resource_intents WHERE execution_id = ? AND idempotency_key = ?",
                (execution_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["kind"] != kind
                    or existing["fingerprint"] != fingerprint
                    or existing["payload_json"] != encoded
                ):
                    raise ExecutionConflict("resource_intent_collision", "resource idempotency key was reused with different content")
                return self._resource_intent(existing)
            intent_id = f"resource-intent-{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO execution_resource_intents (intent_id, execution_id, kind, idempotency_key, fingerprint, admission_id, attempt_id, generation, resource_lease_generation, payload_json, state, claim_owner, claim_expires_at, attempts, result_json, last_error, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, 0, '{}', NULL, ?, ?, NULL)",
                (intent_id, execution_id, kind, idempotency_key, fingerprint, admission_id, attempt_id, generation, resource_lease_generation, encoded, now, now),
            )
            row = connection.execute(
                "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            assert row is not None
            intent = self._resource_intent(row)
            self._append_event(
                connection,
                execution_id=execution_id,
                execution_version=execution.status_version,
                kind=kind,
                payload={"intent": intent},
                created_at=now,
            )
            return intent

    def list_resource_intents(
        self, *, execution_id: str | None = None, states: Collection[str] = (), limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM execution_resource_intents WHERE 1 = 1"
        values: list[Any] = []
        if execution_id is not None:
            query += " AND execution_id = ?"
            values.append(execution_id)
        if states:
            query += " AND state IN (" + ",".join("?" for _ in states) + ")"
            values.extend(states)
        query += " ORDER BY created_at, intent_id"
        if limit is not None:
            if limit <= 0:
                raise ExecutionConflict("invalid_limit", "limit must be positive")
            query += " LIMIT ?"
            values.append(limit)
        with closing(self._connect()) as connection:
            return [self._resource_intent(row) for row in connection.execute(query, values)]

    def claim_resource_intents(
        self, *, owner_id: str, limit: int = 100, lease_ttl_seconds: float = 30.0, now: float | None = None,
    ) -> list[dict[str, Any]]:
        if not owner_id or limit <= 0 or lease_ttl_seconds <= 0:
            raise ExecutionConflict("invalid_resource_claim", "owner_id, limit and lease_ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._transaction() as connection:
            connection.execute(
                "UPDATE execution_resource_intents SET state = 'pending', claim_owner = NULL, claim_expires_at = NULL, updated_at = ? WHERE state = 'claimed' AND claim_expires_at <= ?",
                (current, current),
            )
            rows = connection.execute(
                "SELECT intent_id FROM execution_resource_intents WHERE state = 'pending' ORDER BY created_at, intent_id LIMIT ?", (limit,)
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                changed = connection.execute(
                    "UPDATE execution_resource_intents SET state = 'claimed', claim_owner = ?, claim_expires_at = ?, attempts = attempts + 1, updated_at = ? WHERE intent_id = ? AND state = 'pending'",
                    (owner_id, current + lease_ttl_seconds, current, row["intent_id"]),
                ).rowcount
                if changed:
                    claimed_row = connection.execute(
                        "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (row["intent_id"],)
                    ).fetchone()
                    assert claimed_row is not None
                    claimed.append(self._resource_intent(claimed_row))
            return claimed

    def complete_resource_intent(
        self, intent_id: str, *, owner_id: str, result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "applied":
                return self._resource_intent(row)
            if row["state"] != "claimed" or row["claim_owner"] != owner_id:
                return None
            connection.execute(
                "UPDATE execution_resource_intents SET state = 'applied', claim_owner = NULL, claim_expires_at = NULL, result_json = ?, updated_at = ?, completed_at = ? WHERE intent_id = ?",
                (_json(dict(result or {})), now, now, intent_id),
            )
            return self._resource_intent(connection.execute(
                "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone())

    def retry_resource_intent(self, intent_id: str, *, owner_id: str, error: str) -> bool:
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE execution_resource_intents SET state = 'pending', claim_owner = NULL, claim_expires_at = NULL, last_error = ?, updated_at = ? WHERE intent_id = ? AND state = 'claimed' AND claim_owner = ?",
                (error[:1024], time.time(), intent_id, owner_id),
            ).rowcount == 1

    def list_projection_outbox(
        self,
        *,
        execution_id: str | None = None,
        states: Collection[str] = (),
        limit: int | None = None,
    ):
        query = "SELECT * FROM execution_projection_outbox WHERE 1 = 1"
        values: list[Any] = []
        if execution_id is not None:
            query += " AND execution_id = ?"
            values.append(execution_id)
        if states:
            query += " AND state IN (" + ",".join("?" for _ in states) + ")"
            values.extend(
                getattr(state, "value", state)
                for state in states
            )
        query += " ORDER BY event_sequence, projection_kind"
        if limit is not None:
            if limit <= 0:
                raise ProjectionConflict("invalid_limit", "limit must be positive")
            query += " LIMIT ?"
            values.append(limit)
        with closing(self._connect()) as connection:
            return [
                self._projection_outbox(row)
                for row in connection.execute(query, values)
            ]

    def claim_projection_outbox(
        self,
        *,
        owner_id: str,
        limit: int = 100,
        lease_ttl_seconds: float = 30.0,
        allowed_kinds: Collection[str] | None = None,
    ):
        from .outbox import ProjectionOutboxState

        if not owner_id or limit <= 0 or lease_ttl_seconds <= 0:
            raise ProjectionConflict(
                "invalid_claim", "owner_id, positive limit and lease are required"
            )
        kinds = (
            tuple(getattr(kind, "value", kind) for kind in allowed_kinds)
            if allowed_kinds is not None
            else PROJECTION_KINDS
        )
        if any(kind not in PROJECTION_KINDS for kind in kinds):
            raise ProjectionConflict("invalid_projection_kind", "unknown projection kind")
        if allowed_kinds is not None and not kinds:
            return []
        now = time.time()
        with self._transaction() as connection:
            self._reclaim_projection_outbox_in_transaction(connection, now=now)
            kind_placeholders = ",".join("?" for _ in kinds)
            rows = connection.execute(
                "SELECT * FROM execution_projection_outbox "
                "WHERE state = ? AND available_at <= ? "
                f"AND projection_kind IN ({kind_placeholders}) "
                "ORDER BY event_sequence, projection_kind LIMIT ?",
                (ProjectionOutboxState.PENDING.value, now, *kinds, limit),
            ).fetchall()
            expires = now + lease_ttl_seconds
            claimed = []
            for row in rows:
                updated = connection.execute(
                    "UPDATE execution_projection_outbox SET state = ?, "
                    "claim_owner = ?, claim_expires_at = ?, attempts = attempts + 1 "
                    "WHERE outbox_id = ? AND state = ?",
                    (
                        ProjectionOutboxState.CLAIMED.value,
                        owner_id,
                        expires,
                        row["outbox_id"],
                        ProjectionOutboxState.PENDING.value,
                    ),
                )
                if updated.rowcount == 1:
                    claimed_row = connection.execute(
                        "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                        (row["outbox_id"],),
                    ).fetchone()
                    claimed.append(self._projection_outbox(claimed_row))
            return claimed

    def ack_projection_outbox(self, outbox_id: str, *, owner_id: str):
        from .outbox import ProjectionOutboxState

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise ProjectionConflict("not_found", f"outbox item not found: {outbox_id}")
            if row["state"] == ProjectionOutboxState.DELIVERED.value:
                return self._projection_outbox(row)
            now = time.time()
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL, delivered_at = ?, last_error = NULL "
                "WHERE outbox_id = ? AND state = ? AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ?",
                (
                    ProjectionOutboxState.DELIVERED.value,
                    now,
                    outbox_id,
                    ProjectionOutboxState.CLAIMED.value,
                    owner_id,
                    now,
                ),
            )
            if updated.rowcount != 1:
                latest = connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                assert latest is not None
                self._raise_projection_claim_conflict(latest, owner_id, now)
            return self._projection_outbox(
                connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )

    def fail_projection_outbox(
        self,
        outbox_id: str,
        *,
        owner_id: str,
        error: str,
        retry_at: float | None = None,
    ):
        from .outbox import ProjectionOutboxState

        if not error:
            raise ProjectionConflict("invalid_error", "projection failure must include an error")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise ProjectionConflict("not_found", f"outbox item not found: {outbox_id}")
            now = time.time()
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL, available_at = ?, last_error = ? "
                "WHERE outbox_id = ? AND state = 'claimed' AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ?",
                (
                    ProjectionOutboxState.PENDING.value,
                    now if retry_at is None else retry_at,
                    error[:2000],
                    outbox_id,
                    owner_id,
                    now,
                ),
            )
            if updated.rowcount != 1:
                latest = connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                assert latest is not None
                self._raise_projection_claim_conflict(latest, owner_id, now)
            return self._projection_outbox(
                connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )

    def release_projection_outbox(
        self, outbox_ids: Collection[str], *, owner_id: str
    ) -> int:
        """Return this owner's unprocessed live claims to pending without retry cost."""
        from .outbox import ProjectionOutboxState

        ids = tuple(dict.fromkeys(outbox_ids))
        if not owner_id:
            raise ProjectionConflict("invalid_claim", "owner_id is required")
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        now = time.time()
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL WHERE state = ? AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ? "
                f"AND outbox_id IN ({placeholders})",
                (
                    ProjectionOutboxState.PENDING.value,
                    ProjectionOutboxState.CLAIMED.value,
                    owner_id,
                    now,
                    *ids,
                ),
            )
            return updated.rowcount

    def reclaim_projection_outbox(self, *, now: float | None = None) -> int:
        with self._transaction() as connection:
            return self._reclaim_projection_outbox_in_transaction(
                connection, now=time.time() if now is None else now
            )

    @staticmethod
    def _reclaim_projection_outbox_in_transaction(
        connection: sqlite3.Connection, *, now: float
    ) -> int:
        from .outbox import ProjectionOutboxState

        updated = connection.execute(
            "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
            "claim_expires_at = NULL WHERE state = ? AND claim_expires_at IS NOT NULL "
            "AND claim_expires_at <= ?",
            (
                ProjectionOutboxState.PENDING.value,
                ProjectionOutboxState.CLAIMED.value,
                now,
            ),
        )
        return updated.rowcount

    @staticmethod
    def _raise_projection_claim_conflict(
        row: sqlite3.Row, owner_id: str, now: float
    ) -> None:
        from .outbox import ProjectionOutboxState

        if row["state"] != ProjectionOutboxState.CLAIMED.value:
            raise ProjectionConflict("invalid_state", f"outbox item is {row['state']}")
        if not owner_id or row["claim_owner"] != owner_id:
            raise ProjectionConflict("claim_owner_mismatch", "outbox claim belongs to another owner")
        if row["claim_expires_at"] is None or float(row["claim_expires_at"]) <= now:
            raise ProjectionConflict("claim_expired", "outbox claim has expired")

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
            return [self._event(row) for row in rows]

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
        )

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
        if target in TERMINAL_EXECUTION_STATUSES:
            connection.execute(
                "DELETE FROM execution_finish_repair_slots WHERE execution_id = ?",
                (execution_id,),
            )
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
    ) -> int:
        cursor = connection.execute(
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
    def _execution_input(row: sqlite3.Row) -> ExecutionInputRecord:
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
    def _projection_outbox(row: sqlite3.Row):
        from .outbox import ProjectionOutboxRecord, ProjectionOutboxState

        return ProjectionOutboxRecord(
            outbox_id=str(row["outbox_id"]),
            event_sequence=int(row["event_sequence"]),
            execution_id=str(row["execution_id"]),
            projection_kind=str(row["projection_kind"]),
            dedupe_key=str(row["dedupe_key"]),
            payload_ref=str(row["payload_ref"]),
            state=ProjectionOutboxState(str(row["state"])),
            claim_owner=row["claim_owner"],
            claim_expires_at=(
                float(row["claim_expires_at"])
                if row["claim_expires_at"] is not None
                else None
            ),
            attempts=int(row["attempts"]),
            available_at=float(row["available_at"]),
            delivered_at=(
                float(row["delivered_at"]) if row["delivered_at"] is not None else None
            ),
            last_error=row["last_error"],
        )

    @staticmethod
    def _resource_intent(row: sqlite3.Row | None) -> dict[str, Any]:
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
