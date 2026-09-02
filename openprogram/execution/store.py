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


def _validate_agent_turn_payload(payload: Mapping[str, Any]) -> None:
    """Validate the durable Agent envelope without importing Agent runtime."""
    if not isinstance(payload, Mapping):
        raise ExecutionConflict("invalid_agent_input", "Agent turn input must be an object")
    if "kind" not in payload and "version" not in payload:
        return  # pre-cutover internal execution records
    if payload.get("version") != _AGENT_TURN_INPUT_VERSION:
        raise ExecutionConflict("invalid_agent_input_version", "unsupported Agent turn input version")
    kind = payload.get("kind")
    if kind not in _AGENT_TURN_INPUT_KINDS:
        raise ExecutionConflict("invalid_agent_input_kind", "Agent turn input kind must be chat or forced_tool")
    encoded = _json(payload)
    if len(encoded.encode("utf-8")) > _AGENT_TURN_INPUT_MAX_BYTES:
        raise ExecutionConflict("agent_input_too_large", "Agent turn input exceeds the size limit")


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
    ) -> ExecutionRecord:
        """Admit one execution and its immutable input in one transaction."""
        if not input_ref or not input_hash or not entrypoint or not config_snapshot_ref:
            raise ExecutionConflict(
                "invalid_input", "input_ref, input_hash, entrypoint and config_snapshot_ref are required"
            )
        if not isinstance(trusted_actor, Mapping):
            raise ExecutionConflict("invalid_actor", "trusted_actor must be an object")
        actor_snapshot = _snapshot_json(trusted_actor)
        if agent_turn_payload is not None and not isinstance(agent_turn_payload, Mapping):
            raise ExecutionConflict("invalid_agent_input", "Agent turn input must be an object")
        if agent_turn_payload is not None:
            _validate_agent_turn_payload(agent_turn_payload)
        agent_payload_json = (
            _json(dict(agent_turn_payload)) if agent_turn_payload is not None else None
        )
        with self._transaction() as connection:
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
                if agent_payload_json is not None:
                    connection.execute(
                        "INSERT INTO execution_agent_turn_inputs "
                        "(execution_id, payload_json, content_hash, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            record.execution_id,
                            agent_payload_json,
                            hashlib.sha256(agent_payload_json.encode("utf-8")).hexdigest(),
                            record.created_at,
                        ),
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
        _validate_agent_turn_payload(payload)
        return _snapshot_json(payload)

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
