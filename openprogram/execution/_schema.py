"""SQLite schema for the canonical execution-control store."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .model import CapabilitySet


SCHEMA_VERSION = 6
_LEGACY_SCHEMA_VERSION = 1
_PREVIOUS_SCHEMA_VERSION = 2
_FORK_RETRY_SCHEMA_VERSION = 3
_EXECUTION_CONTROL_SCHEMA_VERSION = 4
_PROJECTION_OUTBOX_SCHEMA_VERSION = 5

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
    elif current == SCHEMA_VERSION:
        _create_current_schema(connection)
    else:
        raise UnsupportedSchema(current)

    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _create_current_schema(connection: sqlite3.Connection) -> None:
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
            execution_version INTEGER,
            command_id TEXT,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            schema_version INTEGER NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
        );
        CREATE INDEX IF NOT EXISTS events_execution_sequence
            ON execution_events(execution_id, sequence);

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
        _backfill_projection_outbox(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
