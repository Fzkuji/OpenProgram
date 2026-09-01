"""SQLite schema for the canonical execution-control store."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 1


class UnsupportedSchema(RuntimeError):
    def __init__(self, version: int):
        self.version = version
        super().__init__(f"unsupported execution store schema: {version}")


def initialize_schema(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current not in (0, SCHEMA_VERSION):
        raise UnsupportedSchema(current)
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
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
