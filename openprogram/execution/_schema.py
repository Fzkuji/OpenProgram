"""SQLite schema for the canonical execution-control store."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .model import CapabilitySet


SCHEMA_VERSION = 13
_LEGACY_SCHEMA_VERSION = 1
_PREVIOUS_SCHEMA_VERSION = 2
_FORK_RETRY_SCHEMA_VERSION = 3
_EXECUTION_CONTROL_SCHEMA_VERSION = 4
_PROJECTION_OUTBOX_SCHEMA_VERSION = 5
_PROJECTION_READ_SCHEMA_VERSION = 6
_FINISH_REPAIR_SLOT_SCHEMA_VERSION = 7
_AGENT_STATE_BLOB_SCHEMA_VERSION = 8
_AGENT_STATE_BLOB_REFERENCE_SCHEMA_VERSION = 9
_RESOURCE_SAGA_SCHEMA_VERSION = 10
_JOB_DURABLE_SCHEMA_VERSION = 11
_PARTIAL_RUNTIME_CONTROL_SCHEMA_VERSION = 12
_FINISH_REPAIR_SLOT_LIMIT = 4096

# These are the only projections emitted by the canonical execution store.
# Adding a projection is a schema/design change, not a caller-defined label.
PROJECTION_KINDS = ("dag", "job", "workflow", "ui")


class UnsupportedSchema(RuntimeError):
    def __init__(self, version: int, reason: str | None = None):
        self.version = version
        self.reason = reason
        message = f"unsupported execution store schema: {version}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)


def initialize_schema(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current == 0:
        _create_current_schema(connection)
    elif current == _LEGACY_SCHEMA_VERSION:
        _migrate_v1(connection)
    elif current == _PREVIOUS_SCHEMA_VERSION:
        _migrate_v2(connection)
    elif current == _FORK_RETRY_SCHEMA_VERSION:
        _migrate_v3(connection)
    elif current == _EXECUTION_CONTROL_SCHEMA_VERSION:
        _migrate_v4(connection)
    elif current == _PROJECTION_OUTBOX_SCHEMA_VERSION:
        _migrate_v5(connection)
    elif current == _PROJECTION_READ_SCHEMA_VERSION:
        _migrate_v6(connection)
    elif current == _FINISH_REPAIR_SLOT_SCHEMA_VERSION:
        _migrate_v7(connection)
    elif current == _AGENT_STATE_BLOB_SCHEMA_VERSION:
        _migrate_v8(connection)
    elif current == _AGENT_STATE_BLOB_REFERENCE_SCHEMA_VERSION:
        _migrate_v9(connection)
    elif current == _RESOURCE_SAGA_SCHEMA_VERSION:
        _migrate_v10(connection)
    elif current in {
        _JOB_DURABLE_SCHEMA_VERSION,
        _PARTIAL_RUNTIME_CONTROL_SCHEMA_VERSION,
    }:
        _migrate_v11(connection)
    elif current == SCHEMA_VERSION:
        _create_current_schema(connection)
    else:
        raise UnsupportedSchema(current)

    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _create_current_schema(connection: sqlite3.Connection) -> None:
    # Existing schemas are upgraded in-place.  Add this column before the
    # CREATE INDEX statements below so v1/v2 bootstraps do not reference a
    # column their historical table did not have yet.
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'execution_events'"
    ).fetchone() is not None:
        _add_column_if_missing(
            connection,
            "execution_events",
            "execution_sequence INTEGER NOT NULL DEFAULT 0",
        )
        _backfill_execution_sequences(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS runs_session_created
            ON runs(session_id, created_at);

        CREATE TABLE IF NOT EXISTS revisions (
            revision_id TEXT PRIMARY KEY,
            parent_revision_id TEXT,
            content_hash TEXT UNIQUE NOT NULL,
            manifest_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(parent_revision_id) REFERENCES revisions(revision_id)
        );

        CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_execution_id TEXT,
            source_checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_version INTEGER NOT NULL,
            reason_code TEXT,
            current_attempt_id TEXT,
            owner_lease_json TEXT NOT NULL,
            checkpoint_head_id TEXT,
            safe_point_json TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            effect_summary_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            terminal_at REAL,
            CHECK(parent_execution_id IS NOT NULL OR source_checkpoint_id IS NULL),
            FOREIGN KEY(parent_execution_id) REFERENCES executions(execution_id)
        );
        CREATE INDEX IF NOT EXISTS executions_session_status
            ON executions(session_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS executions_run_parent
            ON executions(run_id, parent_execution_id, created_at);

        CREATE TABLE IF NOT EXISTS commands (
            command_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            expected_version INTEGER NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            actor_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            result_version INTEGER,
            rejection_code TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        );
        CREATE INDEX IF NOT EXISTS commands_execution_status
            ON commands(execution_id, status, submitted_at);

        CREATE TABLE IF NOT EXISTS execution_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id TEXT NOT NULL,
            execution_sequence INTEGER NOT NULL,
            execution_version INTEGER,
            command_id TEXT,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            schema_version INTEGER NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        );
        CREATE INDEX IF NOT EXISTS events_execution_sequence
            ON execution_events(execution_id, execution_sequence);
        CREATE UNIQUE INDEX IF NOT EXISTS events_execution_cursor_unique
            ON execution_events(execution_id, execution_sequence);

        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            status TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            lease_expires_at REAL NOT NULL,
            leased_at REAL NOT NULL,
            activated_at REAL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            outcome TEXT,
            UNIQUE(execution_id, generation),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        );
        CREATE INDEX IF NOT EXISTS attempts_execution_status
            ON attempts(execution_id, status, generation);

        CREATE TABLE IF NOT EXISTS effects (
            effect_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            classification TEXT NOT NULL,
            idempotency_key TEXT,
            metadata_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            dispatched_at REAL,
            updated_at REAL NOT NULL,
            resolved_at REAL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
        );
        CREATE INDEX IF NOT EXISTS effects_execution_status
            ON effects(execution_id, status, created_at);

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            source_execution_version INTEGER NOT NULL,
            frontier_json TEXT NOT NULL,
            state_refs_json TEXT NOT NULL,
            completed_actions_json TEXT NOT NULL,
            effect_receipts_json TEXT NOT NULL,
            child_frontier_json TEXT NOT NULL,
            completed_frontier_json TEXT,
            pending_commands_json TEXT NOT NULL,
            created_by_attempt_id TEXT NOT NULL,
            content_hash TEXT UNIQUE NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            FOREIGN KEY(parent_checkpoint_id) REFERENCES checkpoints(checkpoint_id),
            FOREIGN KEY(created_by_attempt_id) REFERENCES attempts(attempt_id)
        );
        CREATE INDEX IF NOT EXISTS checkpoints_execution_created
            ON checkpoints(execution_id, created_at);

        """
    )
    _create_projection_schema(connection)
    _create_projection_read_schema(connection)
    _create_agent_input_schema(connection)
    _create_finish_repair_schema(connection)
    _create_agent_state_blob_schema(connection)
    _create_audit_schema(connection)
    _create_agent_state_blob_reference_schema(connection)
    _create_resource_saga_schema(connection)
    _create_revision_control_schema(connection)


def _create_revision_control_schema(connection: sqlite3.Connection) -> None:
    """Create the immutable draft-validation-publication authority."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS revision_artifacts (
            artifact_ref TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content_hash TEXT UNIQUE NOT NULL,
            content_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            CHECK(kind IN (
                'workflow', 'prompt', 'tool_contract', 'model_policy',
                'output_schema', 'program_artifact', 'runtime_contract'
            ))
        );

        CREATE TABLE IF NOT EXISTS revision_drafts (
            draft_id TEXT PRIMARY KEY,
            project_binding_json TEXT NOT NULL,
            source_execution_id TEXT NOT NULL,
            base_revision_id TEXT NOT NULL,
            base_revision_hash TEXT NOT NULL,
            source_checkpoint_id TEXT NOT NULL,
            changes_json TEXT NOT NULL,
            frontier_mapping_json TEXT NOT NULL,
            requested_by_json TEXT NOT NULL,
            draft_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            published_manifest_id TEXT UNIQUE,
            FOREIGN KEY(source_execution_id) REFERENCES executions(execution_id),
            FOREIGN KEY(base_revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(source_checkpoint_id) REFERENCES checkpoints(checkpoint_id),
            CHECK(status IN ('draft', 'published', 'discarded'))
        );
        CREATE INDEX IF NOT EXISTS revision_drafts_source_status
            ON revision_drafts(source_execution_id, status, created_at);

        CREATE TABLE IF NOT EXISTS revision_validations (
            validation_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            draft_version INTEGER NOT NULL,
            report_hash TEXT UNIQUE NOT NULL,
            report_json TEXT NOT NULL,
            compatible_checkpoint_id TEXT NOT NULL,
            proof_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(draft_id) REFERENCES revision_drafts(draft_id),
            FOREIGN KEY(compatible_checkpoint_id) REFERENCES checkpoints(checkpoint_id),
            UNIQUE(draft_id, draft_version),
            CHECK(status = 'valid')
        );

        CREATE TABLE IF NOT EXISTS revision_approvals (
            approval_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            draft_version INTEGER NOT NULL,
            validation_id TEXT NOT NULL,
            validation_report_hash TEXT NOT NULL,
            project_binding_json TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            status TEXT NOT NULL,
            actor_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(draft_id) REFERENCES revision_drafts(draft_id),
            FOREIGN KEY(validation_id) REFERENCES revision_validations(validation_id),
            UNIQUE(draft_id, draft_version),
            CHECK(status IN ('not_required', 'approved'))
        );

        CREATE TABLE IF NOT EXISTS revision_manifests (
            manifest_id TEXT PRIMARY KEY,
            revision_id TEXT UNIQUE NOT NULL,
            source_execution_id TEXT NOT NULL,
            source_checkpoint_id TEXT NOT NULL,
            compatible_checkpoint_id TEXT NOT NULL,
            parent_revision_id TEXT NOT NULL,
            content_hash TEXT UNIQUE NOT NULL,
            manifest_json TEXT NOT NULL,
            validation_id TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            proof_hash TEXT NOT NULL,
            created_by_json TEXT NOT NULL,
            published_at REAL NOT NULL,
            FOREIGN KEY(revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(source_execution_id) REFERENCES executions(execution_id),
            FOREIGN KEY(source_checkpoint_id) REFERENCES checkpoints(checkpoint_id),
            FOREIGN KEY(compatible_checkpoint_id) REFERENCES checkpoints(checkpoint_id),
            FOREIGN KEY(parent_revision_id) REFERENCES revisions(revision_id),
            FOREIGN KEY(validation_id) REFERENCES revision_validations(validation_id),
            FOREIGN KEY(approval_id) REFERENCES revision_approvals(approval_id)
        );
        CREATE INDEX IF NOT EXISTS revision_manifests_source_checkpoint
            ON revision_manifests(source_execution_id, compatible_checkpoint_id);
        """
    )


def _create_resource_saga_schema(connection: sqlite3.Connection) -> None:
    """Create the execution-owned outbox for resource authority intents."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_resource_intents (
            intent_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            admission_id TEXT,
            attempt_id TEXT,
            generation INTEGER,
            resource_lease_generation INTEGER,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL,
            claim_owner TEXT,
            claim_expires_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL,
            UNIQUE(execution_id, idempotency_key),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            CHECK(kind IN (
                'execution.admission.intent', 'resource.admission.intent',
                'execution.claim.intent', 'resource.claim.intent',
                'execution.release.intent', 'resource.release.intent'
            )),
            CHECK(state IN ('pending', 'claimed', 'applied', 'failed'))
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_resource_intents_ready "
        "ON execution_resource_intents(state, claim_expires_at, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_resource_intents_execution "
        "ON execution_resource_intents(execution_id, created_at)"
    )


def _create_projection_schema(connection: sqlite3.Connection) -> None:
    """Create v5 projection tables without forcing a transaction boundary."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_inputs (
            execution_id TEXT PRIMARY KEY,
            input_ref TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            entrypoint TEXT NOT NULL,
            session_id TEXT NOT NULL,
            user_message_id TEXT,
            assistant_message_id TEXT,
            trusted_actor_json TEXT NOT NULL,
            config_snapshot_ref TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_projection_outbox (
            outbox_id TEXT PRIMARY KEY,
            event_sequence INTEGER NOT NULL,
            execution_id TEXT NOT NULL,
            projection_kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            payload_ref TEXT NOT NULL,
            state TEXT NOT NULL,
            claim_owner TEXT,
            claim_expires_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at REAL NOT NULL,
            delivered_at REAL,
            last_error TEXT,
            UNIQUE(event_sequence, projection_kind),
            UNIQUE(dedupe_key),
            FOREIGN KEY(event_sequence) REFERENCES execution_events(sequence),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            CHECK(projection_kind IN ('dag', 'job', 'workflow', 'ui')),
            CHECK(state IN ('pending', 'claimed', 'delivered'))
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_projection_outbox_ready "
        "ON execution_projection_outbox(state, available_at, outbox_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_projection_outbox_execution "
        "ON execution_projection_outbox(execution_id, event_sequence)"
    )


def _create_projection_read_schema(connection: sqlite3.Connection) -> None:
    """Create only read-model tables; canonical lifecycle remains separate."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_projection_events (
            projection_kind TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            execution_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(projection_kind, event_sequence),
            FOREIGN KEY(event_sequence) REFERENCES execution_events(sequence),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            CHECK(projection_kind IN ('dag', 'job', 'workflow', 'ui'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_projection_current (
            projection_kind TEXT NOT NULL,
            execution_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(projection_kind, execution_id),
            FOREIGN KEY(event_sequence) REFERENCES execution_events(sequence),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            CHECK(projection_kind IN ('dag', 'job', 'workflow', 'ui'))
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_projection_current_running "
        "ON execution_projection_current(projection_kind, status, session_id, updated_at)"
    )


def _create_agent_input_schema(connection: sqlite3.Connection) -> None:
    """Persist the complete immutable Agent turn payload for activation."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_agent_turn_inputs (
            execution_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES execution_inputs(execution_id)
        )
        """
    )


def _create_finish_repair_schema(connection: sqlite3.Connection) -> None:
    """Persist Agent terminal writes that need bounded retry/replay."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_finish_repairs (
            execution_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            expected_version INTEGER NOT NULL,
            target TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason_code TEXT,
            command_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(execution_id, attempt_id, generation),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_finish_repairs_updated "
        "ON execution_finish_repairs(updated_at, execution_id)"
    )
    _add_column_if_missing(connection, "execution_finish_repairs", "command_id TEXT")
    _add_column_if_missing(
        connection, "execution_finish_repairs",
        "retry_count INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection, "execution_finish_repairs",
        "next_attempt_at REAL NOT NULL DEFAULT 0",
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_finish_repair_slots (
            execution_id TEXT PRIMARY KEY,
            reserved_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'reserved',
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_finish_repair_slots_reserved "
        "ON execution_finish_repair_slots(reserved_at, execution_id)"
    )
    _add_column_if_missing(
        connection, "execution_finish_repair_slots",
        "state TEXT NOT NULL DEFAULT 'reserved'",
    )


def _create_agent_state_blob_schema(connection: sqlite3.Connection) -> None:
    """Store execution-owned content-addressed Agent checkpoint state."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_state_blobs (
            execution_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            payload BLOB NOT NULL,
            byte_length INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(execution_id, ref),
            UNIQUE(execution_id, sha256),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
            CHECK(ref GLOB 'execstate://sha256/[0-9a-f]*'),
            CHECK(byte_length >= 0),
            CHECK(schema_version >= 1)
        )
        """
    )


def _create_agent_state_blob_reference_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_state_blob_refs (
            execution_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            name TEXT NOT NULL,
            reference_kind TEXT NOT NULL,
            reference_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(execution_id, ref, reference_kind, reference_id),
            FOREIGN KEY(execution_id, ref)
              REFERENCES execution_state_blobs(execution_id, ref)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_state_blobs_execution_created "
        "ON execution_state_blobs(execution_id, created_at)"
    )


def _create_audit_schema(connection: sqlite3.Connection) -> None:
    """Create the immutable, redacted execution audit ledger."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_audit_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_id TEXT UNIQUE NOT NULL,
            execution_id TEXT NOT NULL,
            command_id TEXT,
            draft_id TEXT,
            wait_id TEXT,
            correlation_id TEXT,
            actor_json TEXT NOT NULL,
            surface TEXT NOT NULL,
            action TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            project_binding_json TEXT NOT NULL,
            source_version INTEGER,
            checkpoint_id TEXT,
            result TEXT NOT NULL,
            reason_code TEXT,
            redacted_payload_json TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS execution_audit_execution_sequence "
        "ON execution_audit_events(execution_id, sequence)"
    )


def _backfill_finish_repair_slots(connection: sqlite3.Connection) -> None:
    """Reserve one slot for every nonterminal admitted Agent execution."""
    rows = connection.execute(
        """
        SELECT e.execution_id, e.created_at, e.updated_at,
               EXISTS(
                   SELECT 1 FROM execution_finish_repairs AS r
                   WHERE r.execution_id = e.execution_id
               ) AS has_repair,
               EXISTS(
                   SELECT 1 FROM attempts AS a
                   WHERE a.execution_id = e.execution_id
                     AND a.attempt_id = e.current_attempt_id
                     AND a.status = 'active'
               ) AS has_active_owner
        FROM executions AS e
        JOIN execution_agent_turn_inputs AS i
          ON i.execution_id = e.execution_id
        WHERE e.status NOT IN (?, ?, ?, ?)
        ORDER BY has_repair DESC, has_active_owner DESC,
                 e.created_at, e.execution_id
        """,
        ("completed", "failed", "cancelled", "interrupted"),
    ).fetchall()
    selected = rows[:_FINISH_REPAIR_SLOT_LIMIT]
    # A v7 store may already contain slots for every live Agent execution.
    # Reconcile that state before inserting the bounded selection so overflow
    # executions do not continue to consume admission capacity.
    connection.execute(
        """
        DELETE FROM execution_finish_repair_slots
        WHERE execution_id NOT IN (
            SELECT e.execution_id
            FROM executions AS e
            JOIN execution_agent_turn_inputs AS i
              ON i.execution_id = e.execution_id
            WHERE e.status NOT IN (?, ?, ?, ?)
            ORDER BY EXISTS(
                         SELECT 1 FROM execution_finish_repairs AS r
                         WHERE r.execution_id = e.execution_id
                     ) DESC,
                     EXISTS(
                         SELECT 1 FROM attempts AS a
                         WHERE a.execution_id = e.execution_id
                           AND a.attempt_id = e.current_attempt_id
                           AND a.status = 'active'
                     ) DESC,
                     e.created_at, e.execution_id
            LIMIT ?
        )
        """,
        (
            "completed", "failed", "cancelled", "interrupted",
            _FINISH_REPAIR_SLOT_LIMIT,
        ),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO execution_finish_repair_slots "
        "(execution_id, reserved_at, updated_at, state) VALUES (?, ?, ?, 'reserved')",
        [(row["execution_id"], row["created_at"], row["updated_at"]) for row in selected],
    )
    for row in rows[_FINISH_REPAIR_SLOT_LIMIT:]:
        connection.execute(
            "UPDATE executions SET status = 'reconciliation_required', "
            "status_version = status_version + 1, "
            "reason_code = 'finish_repair_capacity_migration', "
            "updated_at = ? WHERE execution_id = ? "
            "AND status NOT IN (?, ?, ?, ?)",
            (
                row["updated_at"], row["execution_id"],
                "completed", "failed", "cancelled", "interrupted",
            ),
        )


def _migrate_v7(connection: sqlite3.Connection) -> None:
    """Add Agent finish slots and idempotently backfill live executions."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _FINISH_REPAIR_SLOT_SCHEMA_VERSION,
            "cannot migrate finish repair slots inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_agent_input_schema(connection)
        _create_finish_repair_schema(connection)
        _create_agent_state_blob_schema(connection)
        _backfill_finish_repair_slots(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v6(connection: sqlite3.Connection) -> None:
    """Add durable Agent activation payloads without rewriting executions."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _PROJECTION_READ_SCHEMA_VERSION,
            "cannot migrate agent inputs inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_agent_input_schema(connection)
        _create_finish_repair_schema(connection)
        _create_agent_state_blob_schema(connection)
        _backfill_finish_repair_slots(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v5(connection: sqlite3.Connection) -> None:
    """Install replayable read models and replay every fixed projection once."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _PROJECTION_OUTBOX_SCHEMA_VERSION,
            "cannot migrate projection read models inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_projection_read_schema(connection)
        _create_agent_input_schema(connection)
        _create_finish_repair_schema(connection)
        _create_agent_state_blob_schema(connection)
        _backfill_finish_repair_slots(connection)
        # v5 had no durable consumers.  Requeue its fixed delivery set so the
        # new idempotent handlers materialize every historical event.
        connection.execute(
            "UPDATE execution_projection_outbox SET state = 'pending', "
            "claim_owner = NULL, claim_expires_at = NULL, delivered_at = NULL"
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _backfill_projection_outbox(connection: sqlite3.Connection) -> None:
    """Materialize one pending projection per historical canonical event."""
    for projection_kind in PROJECTION_KINDS:
        connection.execute(
            "INSERT OR IGNORE INTO execution_projection_outbox ("
            "outbox_id, event_sequence, execution_id, projection_kind, "
            "dedupe_key, payload_ref, state, attempts, available_at) "
            "SELECT 'outbox_' || sequence || '_' || ?, sequence, execution_id, ?, "
            "'execution-event:' || sequence || ':' || ?, "
            "'execution-event:' || sequence, 'pending', 0, created_at "
            "FROM execution_events",
            (projection_kind, projection_kind, projection_kind),
        )


def _migrate_v4(connection: sqlite3.Connection) -> None:
    """Upgrade a real v4 store and backfill all current projection storage."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _EXECUTION_CONTROL_SCHEMA_VERSION,
            "cannot migrate projection outbox inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_projection_schema(connection)
        _create_projection_read_schema(connection)
        _create_agent_input_schema(connection)
        _create_finish_repair_schema(connection)
        _create_agent_state_blob_schema(connection)
        _backfill_finish_repair_slots(connection)
        _backfill_projection_outbox(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v8(connection: sqlite3.Connection) -> None:
    """Add state blobs without interpreting historic checkpoints."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _AGENT_STATE_BLOB_SCHEMA_VERSION,
            "cannot migrate Agent state blobs inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_agent_state_blob_schema(connection)
        _create_agent_state_blob_reference_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v9(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise UnsupportedSchema(_AGENT_STATE_BLOB_REFERENCE_SCHEMA_VERSION, "cannot migrate state refs inside an active transaction")
    try:
        connection.execute("BEGIN")
        _create_agent_state_blob_schema(connection)
        _create_agent_state_blob_reference_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v10(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise UnsupportedSchema(_RESOURCE_SAGA_SCHEMA_VERSION, "cannot migrate resource saga inside an active transaction")
    try:
        connection.execute("BEGIN")
        _create_resource_saga_schema(connection)
        _create_revision_control_schema(connection)
        _add_column_if_missing(
            connection,
            "execution_events",
            "execution_sequence INTEGER NOT NULL DEFAULT 0",
        )
        _backfill_execution_sequences(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS events_execution_cursor_unique "
            "ON execution_events(execution_id, execution_sequence)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS events_execution_sequence "
            "ON execution_events(execution_id, execution_sequence)"
        )
        _create_audit_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v11(connection: sqlite3.Connection) -> None:
    """Complete both revision and cursor/audit additions from any partial v12."""
    if connection.in_transaction:
        raise UnsupportedSchema(
            _JOB_DURABLE_SCHEMA_VERSION,
            "cannot migrate runtime control schema inside an active transaction",
        )
    try:
        connection.execute("BEGIN")
        _create_revision_control_schema(connection)
        _add_column_if_missing(
            connection,
            "execution_events",
            "execution_sequence INTEGER NOT NULL DEFAULT 0",
        )
        _backfill_execution_sequences(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS events_execution_cursor_unique "
            "ON execution_events(execution_id, execution_sequence)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS events_execution_sequence "
            "ON execution_events(execution_id, execution_sequence)"
        )
        _create_audit_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v1(connection: sqlite3.Connection) -> None:
    _require_v1_tables(connection)
    _create_current_schema(connection)
    _migrate_v2(connection)
    _migrate_v1_capabilities(connection)
    _migrate_v1_runs(connection)
    _migrate_v1_revisions(connection)
    _backfill_execution_sequences(connection)
    _create_audit_schema(connection)


def _backfill_execution_sequences(connection: sqlite3.Connection) -> None:
    counters: dict[str, int] = {}
    for row in connection.execute(
        "SELECT sequence, execution_id FROM execution_events ORDER BY sequence"
    ):
        execution_id = str(row["execution_id"])
        next_sequence = counters.get(execution_id, 0) + 1
        counters[execution_id] = next_sequence
        connection.execute(
            "UPDATE execution_events SET execution_sequence = ? WHERE sequence = ?",
            (next_sequence, row["sequence"]),
        )


def _migrate_v2(connection: sqlite3.Connection) -> None:
    """Add the fork/retry fields without rewriting existing records."""
    _add_column_if_missing(
        connection,
        "executions",
        "source_checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id)",
    )
    _add_column_if_missing(
        connection,
        "commands",
        "result_json TEXT NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing(
        connection,
        "checkpoints",
        "completed_frontier_json TEXT",
    )
    _create_current_schema(connection)
    # v1/v2 executions already exist, so CREATE TABLE IF NOT EXISTS cannot
    # add the v4 source-checkpoint FK and root invariant to that table.
    _migrate_v3(connection)


def _migrate_v3(connection: sqlite3.Connection) -> None:
    """Rebuild historical v3 executions with the source-checkpoint FK.

    v3 databases may have acquired ``source_checkpoint_id`` with an ALTER
    TABLE that did not leave the intended constraint in the schema.  SQLite
    cannot add a table constraint in place, so copy the rows into the
    canonical table definition.  References from other tables continue to
    name ``executions`` because the old table is dropped before the temporary
    table is renamed.
    """
    _create_current_schema(connection)
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(executions)")
    }
    required = {
        "execution_id",
        "run_id",
        "session_id",
        "parent_execution_id",
        "source_checkpoint_id",
        "revision_id",
        "status",
        "status_version",
        "reason_code",
        "current_attempt_id",
        "owner_lease_json",
        "checkpoint_head_id",
        "safe_point_json",
        "capabilities_json",
        "effect_summary_json",
        "created_at",
        "updated_at",
        "terminal_at",
    }
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        raise UnsupportedSchema(
            _FORK_RETRY_SCHEMA_VERSION,
            f"cannot migrate missing executions columns: {missing}",
        )
    orphan = connection.execute(
        "SELECT execution_id, source_checkpoint_id FROM executions "
        "WHERE source_checkpoint_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM checkpoints WHERE checkpoints.checkpoint_id = "
        "executions.source_checkpoint_id) LIMIT 1"
    ).fetchone()
    if orphan is not None:
        raise UnsupportedSchema(
            _FORK_RETRY_SCHEMA_VERSION,
            "cannot migrate execution with missing source checkpoint "
            f"{orphan[1]}",
        )
    invalid_root = connection.execute(
        "SELECT execution_id FROM executions "
        "WHERE parent_execution_id IS NULL AND source_checkpoint_id IS NOT NULL "
        "LIMIT 1"
    ).fetchone()
    if invalid_root is not None:
        raise UnsupportedSchema(
            _FORK_RETRY_SCHEMA_VERSION,
            "cannot migrate root execution with a source checkpoint "
            f"{invalid_root[0]}",
        )

    previous_foreign_keys = bool(
        connection.execute("PRAGMA foreign_keys").fetchone()[0]
    )
    if connection.in_transaction:
        raise UnsupportedSchema(
            _FORK_RETRY_SCHEMA_VERSION,
            "cannot migrate executions inside an active transaction",
        )
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        connection.execute(_execution_table_sql("executions_v4_new"))
        connection.execute(
            "INSERT INTO executions_v4_new ("
            "execution_id, run_id, session_id, parent_execution_id, "
            "source_checkpoint_id, revision_id, status, status_version, "
            "reason_code, current_attempt_id, owner_lease_json, "
            "checkpoint_head_id, safe_point_json, capabilities_json, "
            "effect_summary_json, created_at, updated_at, terminal_at) "
            "SELECT execution_id, run_id, session_id, parent_execution_id, "
            "source_checkpoint_id, revision_id, status, status_version, "
            "reason_code, current_attempt_id, owner_lease_json, "
            "checkpoint_head_id, safe_point_json, capabilities_json, "
            "effect_summary_json, created_at, updated_at, terminal_at "
            "FROM executions"
        )
        connection.execute("DROP TABLE executions")
        connection.execute(
            "ALTER TABLE executions_v4_new RENAME TO executions"
        )
        connection.execute(
            "CREATE INDEX executions_session_status "
            "ON executions(session_id, status, updated_at)"
        )
        connection.execute(
            "CREATE INDEX executions_run_parent "
            "ON executions(run_id, parent_execution_id, created_at)"
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute(
            "PRAGMA foreign_keys = "
            f"{'ON' if previous_foreign_keys else 'OFF'}"
        )
    _backfill_projection_outbox(connection)


def _execution_table_sql(table_name: str) -> str:
    return f"""
        CREATE TABLE {table_name} (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_execution_id TEXT,
            source_checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_version INTEGER NOT NULL,
            reason_code TEXT,
            current_attempt_id TEXT,
            owner_lease_json TEXT NOT NULL,
            checkpoint_head_id TEXT,
            safe_point_json TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            effect_summary_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            terminal_at REAL,
            CHECK(parent_execution_id IS NOT NULL OR source_checkpoint_id IS NULL),
            FOREIGN KEY(parent_execution_id) REFERENCES executions(execution_id)
        )
    """


def _add_column_if_missing(
    connection: sqlite3.Connection, table: str, definition: str
) -> None:
    column = definition.split()[0]
    columns = {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _require_v1_tables(connection: sqlite3.Connection) -> None:
    expected = {
        "executions": {"run_id", "session_id", "revision_id", "capabilities_json"},
        "commands": {"command_id", "execution_id"},
        "execution_events": {"execution_id", "payload_json"},
    }
    for table, columns in expected.items():
        actual = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not columns.issubset(actual):
            raise UnsupportedSchema(
                _LEGACY_SCHEMA_VERSION,
                f"cannot migrate missing or incompatible {table} table",
            )


def _migrate_v1_capabilities(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT execution_id, capabilities_json FROM executions"
    ):
        try:
            capabilities = CapabilitySet.from_dict(json.loads(row["capabilities_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UnsupportedSchema(
                _LEGACY_SCHEMA_VERSION,
                f"cannot migrate capabilities for execution {row['execution_id']}",
            ) from exc
        connection.execute(
            "UPDATE executions SET capabilities_json = ? WHERE execution_id = ?",
            (_json(capabilities.to_dict()), row["execution_id"]),
        )

    for row in connection.execute(
        "SELECT sequence, payload_json FROM execution_events"
    ):
        try:
            payload = json.loads(row["payload_json"])
            record = payload.get("record") if isinstance(payload, dict) else None
            if not isinstance(record, dict) or "capabilities" not in record:
                continue
            record["capabilities"] = CapabilitySet.from_dict(
                record["capabilities"]
            ).to_dict()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UnsupportedSchema(
                _LEGACY_SCHEMA_VERSION,
                f"cannot migrate capabilities in event {row['sequence']}",
            ) from exc
        connection.execute(
            "UPDATE execution_events SET payload_json = ? WHERE sequence = ?",
            (_json(payload), row["sequence"]),
        )


def _migrate_v1_runs(connection: sqlite3.Connection) -> None:
    conflicts = connection.execute(
        "SELECT run_id FROM executions GROUP BY run_id "
        "HAVING COUNT(DISTINCT session_id) != 1"
    ).fetchall()
    if conflicts:
        raise UnsupportedSchema(
            _LEGACY_SCHEMA_VERSION,
            f"cannot migrate run {conflicts[0]['run_id']} with multiple sessions",
        )
    conflicts = connection.execute(
        "SELECT executions.run_id FROM executions JOIN runs "
        "ON runs.run_id = executions.run_id "
        "WHERE runs.session_id != executions.session_id LIMIT 1"
    ).fetchall()
    if conflicts:
        raise UnsupportedSchema(
            _LEGACY_SCHEMA_VERSION,
            f"cannot migrate run {conflicts[0]['run_id']} with multiple sessions",
        )
    connection.execute(
        "INSERT OR IGNORE INTO runs (run_id, session_id, created_at) "
        "SELECT run_id, session_id, MIN(created_at) FROM executions "
        "GROUP BY run_id, session_id"
    )


def _migrate_v1_revisions(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT DISTINCT revision_id FROM executions ORDER BY revision_id"
    ):
        revision_id = str(row["revision_id"])
        manifest = {"legacy_revision_id": revision_id}
        connection.execute(
            "INSERT OR IGNORE INTO revisions "
            "(revision_id, parent_revision_id, content_hash, manifest_json, created_at) "
            "VALUES (?, NULL, ?, ?, 0)",
            (revision_id, _hash(manifest), _json(manifest)),
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
