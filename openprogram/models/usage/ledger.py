"""UsageLedger — append-only SQLite store of UsageEvents.

One global file (``~/.openprogram/usage.db``, profile-aware) with immutable
``usage_events`` and request receipts. Indexed by time, by model+time, by
session, by kind+time. WAL mode so the Agent method subprocesses can append
concurrently with the main worker.

Job admissions, budgets and reservations are run state. The runtime hands the
ledger their tables through :func:`use_run_tables`. A step that changes both
kinds of row commits the event here first and the reservation after, so a stop
in between leaves exposure held and an event to recognise it by, never spend
without a record of it.

Append is idempotent on ``event_id`` (INSERT OR IGNORE) so a retried write
never double-counts. ``query()`` does the grouping/time-bucketing in SQL.

The class is the default backend behind a thin interface (append/query);
a future JSONL or remote backend can implement the same two methods.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .event import UsageEvent

_COLUMNS = [
    "event_id", "ts", "session_id", "parent_session_id", "agent_id",
    "call_kind", "call_label", "origin_pid", "provider", "api", "model_id",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "cost_total", "cost_input", "cost_output",
    "cost_cache_read", "cost_cache_write", "cost_source", "token_source",
    "schema_version", "job_id", "budget_scope_id", "reservation_id",
    "request_id", "goal_id", "goal_revision", "goal_session_id", "execution_id", "tokens_known",
    "first_token_ms", "first_text_ms", "prefix_hash", "break_at", "break_cause", "expected",
]

_BOOTSTRAP_BUSY_TIMEOUT_MS = 2_000
_BOOTSTRAP_RETRIES = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    event_id        TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    session_id      TEXT,
    parent_session_id TEXT,
    agent_id        TEXT,
    call_kind       TEXT NOT NULL,
    call_label      TEXT,
    origin_pid      INTEGER,
    provider        TEXT NOT NULL,
    api             TEXT,
    model_id        TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_total      REAL NOT NULL DEFAULT 0,
    cost_input      REAL,
    cost_output     REAL,
    cost_cache_read REAL,
    cost_cache_write REAL,
    cost_source     TEXT,
    token_source    TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1
    ,job_id         TEXT
    ,budget_scope_id TEXT
    ,reservation_id  TEXT
    ,request_id TEXT
    ,goal_id TEXT
    ,goal_revision INTEGER
    ,goal_session_id TEXT
    ,execution_id TEXT
    ,tokens_known INTEGER NOT NULL DEFAULT 1
    ,first_token_ms INTEGER
    ,first_text_ms INTEGER
    ,prefix_hash TEXT
    ,break_at INTEGER
    ,break_cause TEXT
    ,expected TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_ts       ON usage_events(ts);
CREATE INDEX IF NOT EXISTS ix_usage_model_ts ON usage_events(model_id, ts);
CREATE INDEX IF NOT EXISTS ix_usage_session  ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS ix_usage_kind_ts  ON usage_events(call_kind, ts);
CREATE INDEX IF NOT EXISTS ix_usage_job     ON usage_events(job_id);
CREATE INDEX IF NOT EXISTS ix_usage_goal ON usage_events(goal_session_id, goal_id);
CREATE INDEX IF NOT EXISTS ix_usage_execution ON usage_events(execution_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_usage_request ON usage_events(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_usage_reservation ON usage_events(reservation_id);
CREATE INDEX IF NOT EXISTS ix_usage_budget_scope ON usage_events(budget_scope_id);

-- The last answered request on each prompt-cache lane (session, kind of
-- call, label, agent, branch), as models/usage/prefix_fingerprint.py
-- fingerprints it: the next request on the lane is compared with it, across
-- Worker restarts. ``family`` is the lane without its branch.
CREATE TABLE IF NOT EXISTS prefix_baselines (
    lane TEXT PRIMARY KEY,
    family TEXT,
    session_id TEXT,
    ts REAL NOT NULL,
    fingerprint_json TEXT NOT NULL,
    cache_read INTEGER,
    expected TEXT
);
CREATE INDEX IF NOT EXISTS ix_prefix_baselines_session ON prefix_baselines(session_id);

CREATE TABLE IF NOT EXISTS usage_requests (
    request_id TEXT PRIMARY KEY,
    goal_session_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('prepared','started','settled','released')),
    metadata_json TEXT NOT NULL,
    receipt_json TEXT,
    observed_json TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_requests_goal ON usage_requests(goal_session_id, goal_id);

-- One row per model switch the failover chain made: ``from`` failed before
-- any content streamed and ``to`` was tried next. The routing overview
-- reads these to say why a session's reply came from another model.
CREATE TABLE IF NOT EXISTS route_events (
    event_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    parent_session_id TEXT,
    from_provider TEXT NOT NULL,
    from_model TEXT NOT NULL,
    to_provider TEXT NOT NULL,
    to_model TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_route_events_ts ON route_events(ts);

-- One row per request OpenProgram sent a provider on its own behalf (a
-- connectivity test, a credential check, a failed model list): never
-- usage, so no total reads it. See models/listing/probe_log.py.
CREATE TABLE IF NOT EXISTS probe_events (
    event_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    provider TEXT NOT NULL,
    kind TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    elapsed_ms INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_probe_events_ts ON probe_events(ts);

-- The latest value of each quota limit a provider reported, per provider,
-- key (credential id; '' for a key with no stored credential) and limit.
-- Written by models/usage/quota.py from response headers, the Codex
-- WebSocket frame and the read-only quota endpoints. Never usage.
CREATE TABLE IF NOT EXISTS quota_snapshots (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    limit_key TEXT NOT NULL,
    label TEXT NOT NULL,
    unit TEXT NOT NULL,
    used REAL, lim REAL, remaining REAL, used_fraction REAL,
    window_minutes INTEGER, resets_at REAL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (provider, account, limit_key)
);
-- At most one row per limit per QUOTA_SAMPLE_EVERY_S, kept QUOTA_SAMPLES_KEPT_S:
-- the balance history and a limit's line on a usage chart.
CREATE TABLE IF NOT EXISTS quota_samples (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    limit_key TEXT NOT NULL,
    ts REAL NOT NULL,
    remaining REAL,
    used_fraction REAL
);
CREATE INDEX IF NOT EXISTS ix_quota_samples ON quota_samples(provider, account, limit_key, ts);
-- The last read of each key's quota endpoint, whatever it answered, so every
-- process (Worker, CLI) follows one read policy: when it was read, the
-- failure of that read (NULL when it answered with values), the HTTP status,
-- a 429 hold, and when a failed call last asked for a read.
CREATE TABLE IF NOT EXISTS quota_reads (
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    read_at REAL,
    status INTEGER,
    error TEXT,
    held_until REAL,
    failure_read_at REAL,
    PRIMARY KEY (provider, account)
);
"""

# group_by token → SQL expression. ``day``/``hour`` are time buckets.
_GROUP_EXPR = {
    "model_id": "model_id",
    "provider": "provider",
    "call_kind": "call_kind",
    "call_label": "call_label",
    "session_id": "session_id",
    "agent_id": "agent_id",
    "day": "CAST(ts / 86400 AS INTEGER)",
    "hour": "CAST(ts / 3600 AS INTEGER)",
}


MOVED_TABLES = ("budget_scopes", "job_admissions", "job_finalizations", "usage_reservations")

_run_tables_for_path: Optional[Callable[[Path], Any]] = None


def use_run_tables(resolve: Callable[[Path], Any]) -> None:
    """The runtime names the run tables that belong beside a usage file."""
    global _run_tables_for_path
    _run_tables_for_path = resolve


def _is_sqlite_busy(error: sqlite3.Error) -> bool:
    """Return whether SQLite reported a retryable lock contention."""
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in {
        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(error).lower()
    return "database is locked" in message or "database table is locked" in message


@dataclass
class AggregateRow:
    """One grouped aggregate. ``keys`` maps each requested group_by field to
    its value; the rest are summed metrics."""
    keys: dict
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cost_total: float
    events: int
    cost_known: bool
    unknown_cost_events: int


# How long probe_events rows are kept.
PROBE_EVENTS_KEPT_S = 30 * 24 * 3600
# One quota sample per limit at most this often; samples older than the
# second figure are dropped as new ones come.
QUOTA_SAMPLE_EVERY_S = 15 * 60
QUOTA_SAMPLES_KEPT_S = 90 * 24 * 3600
_QUOTA_FIELDS = (
    "provider", "account", "limit_key", "label", "unit", "used", "lim", "remaining",
    "used_fraction", "window_minutes", "resets_at", "source", "status", "fetched_at",
)
_QUOTA_READ_FIELDS = ("read_at", "status", "error", "held_until", "failure_read_at")


class UsageLedger:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._explicit_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_pid: Optional[int] = None
        self._after_commit: list[Callable[[], Any]] = []
        self._run_tables: Any = None
        # The reader answers for accounting while the run tables are held, so
        # its lock is never held while waiting for any other.
        self._reader_lock = threading.Lock()
        self._reader: Optional[sqlite3.Connection] = None
        self._reader_pid: Optional[int] = None

    # connection (per-process; reopened after fork)

    def _path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        from openprogram.state.paths import get_usage_db_path
        return get_usage_db_path()

    def _connect(self) -> sqlite3.Connection:
        # A connection can't cross a fork; reopen if the pid changed so a
        # Agent method subprocess gets its own handle to the shared db.
        if self._conn is not None and self._conn_pid == os.getpid():
            return self._conn
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(_BOOTSTRAP_RETRIES):
            conn = sqlite3.connect(
                str(path), timeout=_BOOTSTRAP_BUSY_TIMEOUT_MS / 1000,
                check_same_thread=False,
            )
            try:
                # Set this before WAL/schema work.  journal_mode=WAL and
                # BEGIN IMMEDIATE both acquire locks during bootstrap.
                conn.execute(
                    f"PRAGMA busy_timeout={_BOOTSTRAP_BUSY_TIMEOUT_MS}"
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                self._migrate_legacy_job_schema(conn)
                self._migrate_usage_columns(conn)
                conn.executescript(_SCHEMA)
            except sqlite3.Error as exc:
                try:
                    conn.rollback()
                finally:
                    conn.close()
                if not _is_sqlite_busy(exc) or attempt + 1 >= _BOOTSTRAP_RETRIES:
                    raise
                time.sleep(min(0.25, 0.01 * (2**attempt)))
                continue
            except Exception:
                conn.rollback()
                conn.close()
                raise
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._conn_pid = os.getpid()
            return conn
        raise RuntimeError("SQLite bootstrap retries exhausted")

    @staticmethod
    def _migrate_legacy_job_schema(conn: sqlite3.Connection) -> None:
        """Rename persisted async-task tables and columns in place."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP INDEX IF EXISTS ix_usage_task")
            tables = {
                str(row[0]) for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            for old, new in (
                ("task_admissions", "job_admissions"),
                ("task_finalizations", "job_finalizations"),
            ):
                if old in tables and new in tables:
                    old_count = conn.execute(f"SELECT COUNT(*) FROM {old}").fetchone()[0]
                    if old_count:
                        raise RuntimeError(
                            f"cannot migrate legacy {old}: {new} already exists"
                        )
                    conn.execute(f"DROP TABLE {old}")
                    tables.remove(old)
                if old in tables and new not in tables:
                    conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
                    tables.remove(old)
                    tables.add(new)

            def rename_column(table: str, old: str, new: str) -> None:
                if table not in tables:
                    return
                columns = {
                    str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
                }
                if old in columns and new not in columns:
                    conn.execute(
                        f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
                    )

            for table, old, new in (
                ("usage_events", "task_id", "job_id"),
                ("job_admissions", "task_id", "job_id"),
                ("job_admissions", "parent_task_id", "parent_job_id"),
                (
                    "job_admissions",
                    "borrowed_parent_task_id",
                    "borrowed_parent_job_id",
                ),
                ("job_finalizations", "task_id", "job_id"),
                ("usage_reservations", "task_id", "job_id"),
            ):
                rename_column(table, old, new)

            if "budget_scopes" in tables:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(budget_scopes)")
                }
                schema_row = conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'budget_scopes'"
                ).fetchone()
                schema = str(schema_row[0]) if schema_row else ""
                if "task_id" in columns or "'task'" in schema:
                    job_column = "task_id" if "task_id" in columns else "job_id"
                    conn.execute("ALTER TABLE budget_scopes RENAME TO budget_scopes_legacy")
                    conn.execute("""
                        CREATE TABLE budget_scopes (
                            budget_scope_id TEXT PRIMARY KEY,
                            scope_kind TEXT NOT NULL
                                CHECK (scope_kind IN ('session','job')),
                            session_id TEXT NOT NULL,
                            job_id TEXT UNIQUE,
                            parent_scope_id TEXT,
                            max_total_tokens INTEGER,
                            max_cost_microusd INTEGER,
                            max_runtime_seconds INTEGER,
                            idle_timeout_seconds INTEGER,
                            created_at REAL NOT NULL
                        )
                    """)
                    conn.execute(f"""
                        INSERT INTO budget_scopes
                        SELECT budget_scope_id,
                               CASE scope_kind WHEN 'task' THEN 'job'
                                               ELSE scope_kind END,
                               session_id, {job_column}, parent_scope_id,
                               max_total_tokens, max_cost_microusd,
                               max_runtime_seconds, idle_timeout_seconds,
                               created_at
                        FROM budget_scopes_legacy
                    """)
                    conn.execute("DROP TABLE budget_scopes_legacy")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _migrate_usage_columns(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(usage_events)")
            }
            columns = {name: "TEXT" for name in (
                "job_id", "budget_scope_id", "reservation_id", "request_id", "goal_id",
                "goal_session_id", "execution_id", "prefix_hash", "break_cause", "expected")}
            columns.update(goal_revision="INTEGER", tokens_known="INTEGER NOT NULL DEFAULT 1",
                           first_token_ms="INTEGER", first_text_ms="INTEGER", break_at="INTEGER")
            for name, definition in columns.items():
                if existing and name not in existing:
                    conn.execute(f"ALTER TABLE usage_events ADD COLUMN {name} {definition}")
            request_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(usage_requests)")}
            if request_columns and "observed_json" not in request_columns:
                conn.execute("ALTER TABLE usage_requests ADD COLUMN observed_json TEXT")
            baseline_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(prefix_baselines)")}
            if baseline_columns and "family" not in baseline_columns:
                conn.execute("ALTER TABLE prefix_baselines ADD COLUMN family TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def connection(self) -> sqlite3.Connection:
        """Return the process-local connection for governance transactions."""
        with self._lock:
            return self._connect()

    @contextmanager
    def read(self):
        """Serialize a read without letting sqlite's context manager commit."""
        with self._lock:
            yield self._connect()

    @contextmanager
    def immediate(self):
        """Serialize one short cross-process accounting transaction.

        Work given to :meth:`after_commit` runs once the transaction is durable.
        """
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            self._after_commit = []
            try:
                yield conn
            except Exception:
                conn.rollback()
                self._after_commit = []
                raise
            else:
                conn.commit()
                work, self._after_commit = self._after_commit, []
                for step in work:
                    step()

    def after_commit(self, step: Callable[[], Any]) -> None:
        """Run ``step`` after the open :meth:`immediate` transaction commits."""
        self._after_commit.append(step)

    def run_tables(self) -> Any:
        """The run tables beside this usage file."""
        if self._run_tables is None:
            if _run_tables_for_path is None:
                raise RuntimeError("no run tables are registered for the usage ledger")
            self._run_tables = _run_tables_for_path(self._path())
        return self._run_tables

    @staticmethod
    def ensure_job_session(tables, job_id: str) -> None:
        """Bring a Job's session into memory; its budget scope says which it is."""
        scope = tables.first("budget_scopes", job_id=job_id)
        if scope is not None:
            tables.tables.ensure_session(scope["session_id"])

    def open_accounting_reader(self) -> None:
        """Make sure the file exists before anything reads it under the run tables."""
        with self._lock:
            self._connect()

    def _accounting_rows(self, sql: str, parameters: tuple) -> list[sqlite3.Row]:
        with self._reader_lock:
            if self._reader is None or self._reader_pid != os.getpid():
                self._reader = sqlite3.connect(
                    str(self._path()), timeout=_BOOTSTRAP_BUSY_TIMEOUT_MS / 1000,
                    check_same_thread=False,
                )
                self._reader.row_factory = sqlite3.Row
                self._reader_pid = os.getpid()
            return self._reader.execute(sql, parameters).fetchall()

    def scope_usage_events(self, scope_ids: Iterable[str]) -> list[sqlite3.Row]:
        """Tokens and cost of every event charged to these budget scopes."""
        ids = tuple(scope_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        return self._accounting_rows(
            "SELECT total_tokens, cost_total, cost_source FROM usage_events "
            f"WHERE budget_scope_id IN ({marks})", ids,
        )

    def reservation_events(self, reservation_ids: Iterable[str]) -> list[sqlite3.Row]:
        """Events already recorded against these reservations."""
        ids = tuple(reservation_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        return self._accounting_rows(
            "SELECT event_id, reservation_id, tokens_known, cost_source FROM usage_events "
            f"WHERE reservation_id IN ({marks})", ids,
        )

    def stored_run_table_rows(self) -> Optional[dict[str, list[dict]]]:
        """Run-table rows still in this file, by table; None once they are gone."""
        with self._lock:
            conn = self._connect()
            marks = ", ".join("?" for _ in MOVED_TABLES)
            present = {str(row[0]) for row in sqlite3.Connection.execute(
                conn, f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({marks})",
                MOVED_TABLES,
            )}
            if not present:
                return None
            return {
                table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                for table in MOVED_TABLES if table in present
            }

    def drop_stored_run_tables(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in MOVED_TABLES:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    # write

    def append(self, event: UsageEvent) -> None:
        with self._lock:
            conn = self._connect()
            self.append_in_transaction(conn, event)
            conn.commit()

    @staticmethod
    def append_in_transaction(conn: sqlite3.Connection, event: UsageEvent) -> None:
        row = [getattr(event, c) for c in _COLUMNS]
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = (f"INSERT OR IGNORE INTO usage_events ({','.join(_COLUMNS)}) "
               f"VALUES ({placeholders})")
        conn.execute(sql, row)

    @staticmethod
    def check_reservation_startable(tables, reservation_id: str) -> None:
        ids = [reservation_id + ":token", reservation_id + ":cost"]
        now = time.time()
        if tables.exists(
            "usage_reservations", reservation_id=ids, state="reserved",
            where=lambda row: row["expires_at"] is not None and row["expires_at"] <= now,
        ):
            raise RuntimeError("Provider reservation expired before dispatch")
        if not tables.exists("usage_reservations", reservation_id=ids, state=("reserved", "started", "settled")):
            raise KeyError(reservation_id)

    @staticmethod
    def mark_reservation_started(tables, reservation_id: str) -> None:
        tables.update(
            "usage_reservations", {"state": "started", "request_started_at": time.time()},
            reservation_id=[reservation_id + ":token", reservation_id + ":cost"], state="reserved",
        )

    def start_provider_reservation(self, conn, reservation_id: str) -> None:
        """Refuse now; mark the reservation started once the caller's transaction commits.

        Stopping between the two leaves a reservation that expires and is
        released: no provider has been called yet.
        """
        del conn
        run_tables = self.run_tables()
        self.check_reservation_startable(run_tables.handle, reservation_id)

        def mark_started() -> None:
            with run_tables.transaction() as tables:
                self.mark_reservation_started(tables, reservation_id)

        self.after_commit(mark_started)

    def attribute_provider_event(self, reservation_id: str, event: UsageEvent) -> UsageEvent:
        rows = self.run_tables().handle.rows(
            "usage_reservations", reservation_id=[reservation_id + ":token", reservation_id + ":cost"],
        )
        if not rows:
            raise KeyError(reservation_id)
        if any(row["state"] == "released" for row in rows):
            raise RuntimeError("cannot settle a released provider reservation")
        return event.model_copy(update={"job_id": rows[0]["job_id"], "budget_scope_id": rows[0]["budget_scope_id"],
                                        "reservation_id": reservation_id + ":token"})

    def settle_provider_reservation(self, conn, reservation_id: str, event: UsageEvent):
        """Record the event in this transaction and settle the reservation after it commits."""
        attributed = self.attribute_provider_event(reservation_id, event)
        self.append_in_transaction(conn, attributed)
        # A known cost with missing tokens cannot release token exposure, and
        # known tokens cannot make an unknown price free.
        known = [
            reservation_id + ":" + kind
            for kind, is_known in (("token", event.tokens_known), ("cost", event.cost_source != "unknown"))
            if is_known
        ]
        run_tables = self.run_tables()

        def settle() -> None:
            with run_tables.transaction() as tables:
                self.mark_reservations_settled(tables, known, attributed.event_id)

        self.after_commit(settle)
        return attributed

    @staticmethod
    def mark_reservations_settled(tables, reservation_ids: list[str], event_id: str) -> int:
        return tables.update(
            "usage_reservations", {"state": "settled", "settled_event_id": event_id},
            reservation_id=reservation_ids, state=("reserved", "started"),
        )

    def flush_request(self, conn, row):
        event = UsageEvent.model_validate_json(row["receipt_json"])
        metadata = json.loads(row["metadata_json"])
        reservation = metadata.get("job_reservation_id")
        if reservation:
            if metadata.get("job_id"):
                self.ensure_job_session(self.run_tables().handle, metadata["job_id"])
            event = self.settle_provider_reservation(conn, reservation, event)
        else:
            self.append_in_transaction(conn, event)
        conn.execute("UPDATE usage_requests SET state = 'settled' WHERE request_id = ?", (row["request_id"],))
        return event

    def append_many(self, events: Iterable[UsageEvent]) -> None:
        rows = [[getattr(e, c) for c in _COLUMNS] for e in events]
        if not rows:
            return
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = (f"INSERT OR IGNORE INTO usage_events ({','.join(_COLUMNS)}) "
               f"VALUES ({placeholders})")
        with self._lock:
            conn = self._connect()
            conn.executemany(sql, rows)
            conn.commit()

    # read

    def calls(self, *, call_kinds: Iterable[str], since: Optional[float] = None,
              execution_ids: Optional[Iterable[str]] = None) -> list[dict]:
        """Every call of ``call_kinds`` whose tokens are known, oldest first:
        when, which conversation and execution, which model, and its tokens.
        ``since`` keeps the calls from then on; ``execution_ids`` those made
        in one of these executions."""
        kinds = tuple(call_kinds)
        if not kinds:
            return []
        where = [f"tokens_known = 1 AND call_kind IN ({', '.join('?' for _ in kinds)})"]
        params: list = list(kinds)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if execution_ids is not None:
            ids = tuple(execution_ids)
            if not ids:
                return []
            where.append(f"execution_id IN ({', '.join('?' for _ in ids)})")
            params.extend(ids)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT ts, session_id, parent_session_id, execution_id, call_kind, provider, "
                "api, model_id, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens FROM usage_events "
                f"WHERE {' AND '.join(where)} ORDER BY ts",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def goal_usage(self, session_id: str, goal_id: str) -> dict:
        """One snapshot of known subtotals and unresolved request counts."""
        with self.immediate() as conn:
            for pending in conn.execute("""SELECT request_id, receipt_json, metadata_json FROM usage_requests
                    WHERE goal_session_id = ? AND goal_id = ? AND state = 'started' AND receipt_json IS NOT NULL""",
                    (session_id, goal_id)).fetchall():
                self.flush_request(conn, pending)
            row = conn.execute("""
                SELECT COALESCE(SUM(COALESCE(e.total_tokens, json_extract(r.observed_json, '$.total_tokens'), 0)), 0) AS tokens,
                       COALESCE(SUM(COALESCE(e.cost_total, json_extract(r.observed_json, '$.cost_total'), 0)), 0) AS cost,
                       COALESCE(SUM(CASE WHEN e.event_id IS NULL OR e.tokens_known = 0 THEN 1 ELSE 0 END), 0) AS unknown_tokens,
                       COALESCE(SUM(CASE WHEN e.event_id IS NULL OR e.cost_source = 'unknown' THEN 1 ELSE 0 END), 0) AS unknown_cost,
                       COUNT(*) AS requests
                FROM usage_requests r LEFT JOIN usage_events e ON e.request_id = r.request_id
                WHERE r.goal_session_id = ? AND r.goal_id = ? AND r.state IN ('started','settled')
            """, (session_id, goal_id)).fetchone()
        return {"total_tokens": row["tokens"], "cost_usd": row["cost"],
                "tokens_known": row["unknown_tokens"] == 0, "cost_known": row["unknown_cost"] == 0,
                "unknown_token_requests": row["unknown_tokens"], "unknown_cost_requests": row["unknown_cost"],
                "requests": row["requests"]}

    def query(
        self,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
        group_by: Optional[list[str]] = None,
        filters: Optional[dict] = None,
        execution_ids: Optional[Iterable[str]] = None,
    ) -> list[AggregateRow]:
        """Aggregate events. ``group_by`` is a list of field names from
        ``_GROUP_EXPR`` (``day``/``hour`` are time buckets). ``filters`` maps
        an equality column to a value; ``execution_ids`` keeps the events of
        those executions (none at all for an empty set). Unknown group_by
        tokens are ignored."""
        group_by = [g for g in (group_by or []) if g in _GROUP_EXPR]
        where, params = [], []
        if execution_ids is not None:
            ids = tuple(execution_ids)
            where.append(f"execution_id IN ({', '.join('?' for _ in ids)})" if ids else "0")
            params.extend(ids)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if until is not None:
            where.append("ts < ?")
            params.append(until)
        for col, val in (filters or {}).items():
            if col in _COLUMNS:
                where.append(f"{col} = ?")
                params.append(val)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        group_exprs = [f"{_GROUP_EXPR[g]} AS g{i}" for i, g in enumerate(group_by)]
        group_select = (", ".join(group_exprs) + ", ") if group_exprs else ""
        group_clause = (" GROUP BY " + ", ".join(f"g{i}" for i in range(len(group_by)))) \
            if group_by else ""

        sql = (
            f"SELECT {group_select}"
            "SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, "
            "SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, "
            # A row written before the recorder counted cached tokens in its
            # total (DeepSeek: cache reads above the "total") reads as the
            # sum of its parts, the larger of the two, as the recorder now writes.
            "SUM(MAX(COALESCE(total_tokens, 0), COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) "
            "+ COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0))) AS total_tokens, "
            "SUM(cost_total) AS cost_total, "
            "SUM(CASE WHEN COALESCE(cost_source, 'unknown') = 'unknown' "
            "THEN 1 ELSE 0 END) AS unknown_cost_events, "
            "COUNT(*) AS events "
            "FROM usage_events"
            f"{where_sql}{group_clause}"
        )
        with self._lock:
            conn = self._connect()
            cur = conn.execute(sql, params)
            rows = cur.fetchall()

        out: list[AggregateRow] = []
        for r in rows:
            keys = {group_by[i]: r[f"g{i}"] for i in range(len(group_by))}
            out.append(AggregateRow(
                keys=keys,
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
                cache_read_tokens=int(r["cache_read_tokens"] or 0),
                cache_write_tokens=int(r["cache_write_tokens"] or 0),
                total_tokens=int(r["total_tokens"] or 0),
                cost_total=float(r["cost_total"] or 0.0),
                events=int(r["events"] or 0),
                cost_known=int(r["unknown_cost_events"] or 0) == 0,
                unknown_cost_events=int(r["unknown_cost_events"] or 0),
            ))
        return out

    def append_route_event(
        self,
        *,
        from_provider: str,
        from_model: str,
        to_provider: str,
        to_model: str,
        reason: str,
        session_id: Optional[str] = None,
        parent_session_id: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        """Record one failover switch (see the ``route_events`` table)."""
        import uuid
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO route_events (event_id, ts, session_id, parent_session_id, "
                "from_provider, from_model, to_provider, to_model, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, time.time() if ts is None else ts, session_id,
                 parent_session_id, from_provider, from_model, to_provider, to_model, reason),
            )
            conn.commit()

    def route_events(self, *, since: float, limit: int = 50) -> list[dict]:
        """Failover switches since ``since``, newest first."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT ts, session_id, parent_session_id, from_provider, from_model, "
                "to_provider, to_model, reason FROM route_events WHERE ts >= ? "
                "ORDER BY ts DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def append_probe_event(
        self,
        *,
        provider: str,
        kind: str,
        method: str,
        url: str,
        status: Optional[int],
        elapsed_ms: int,
        error: Optional[str],
        ts: Optional[float] = None,
    ) -> None:
        """Record one request sent on OpenProgram's own behalf (see ``probe_events``).
        Rows older than :data:`PROBE_EVENTS_KEPT_S` go as new ones come."""
        import uuid
        now = time.time() if ts is None else ts
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM probe_events WHERE ts < ?", (now - PROBE_EVENTS_KEPT_S,))
            conn.execute(
                "INSERT INTO probe_events (event_id, ts, provider, kind, method, url, "
                "status, elapsed_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, now, provider, kind, method, url, status, elapsed_ms, error),
            )
            conn.commit()

    def probe_events(
        self, *, since: float, provider: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """Requests sent on OpenProgram's own behalf since ``since``, newest first."""
        where, args = "ts >= ?", [since]
        if provider is not None:
            where += " AND provider = ?"
            args.append(provider)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT ts, provider, kind, method, url, status, elapsed_ms, error "
                f"FROM probe_events WHERE {where} ORDER BY ts DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def put_quota_rows(self, rows: list[dict]) -> None:
        """Replace the stored value of each quota limit in ``rows`` and sample it.

        Each row carries the ``quota_snapshots`` columns. A limit gets a
        ``quota_samples`` row when its last one is older than
        :data:`QUOTA_SAMPLE_EVERY_S`.
        """
        if not rows:
            return
        columns = ", ".join(_QUOTA_FIELDS)
        marks = ", ".join("?" for _ in _QUOTA_FIELDS)
        with self._lock:
            conn = self._connect()
            now = max(float(row["fetched_at"]) for row in rows)
            conn.execute("DELETE FROM quota_samples WHERE ts < ?", (now - QUOTA_SAMPLES_KEPT_S,))
            for row in rows:
                conn.execute(
                    f"INSERT OR REPLACE INTO quota_snapshots ({columns}) VALUES ({marks})",
                    tuple(row.get(name) for name in _QUOTA_FIELDS),
                )
                last = conn.execute(
                    "SELECT MAX(ts) FROM quota_samples WHERE provider = ? AND account = ? AND limit_key = ?",
                    (row["provider"], row["account"], row["limit_key"]),
                ).fetchone()[0]
                if last is None or float(row["fetched_at"]) - float(last) >= QUOTA_SAMPLE_EVERY_S:
                    conn.execute(
                        "INSERT INTO quota_samples (provider, account, limit_key, ts, remaining, used_fraction) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (row["provider"], row["account"], row["limit_key"], row["fetched_at"],
                         row.get("remaining"), row.get("used_fraction")),
                    )
            conn.commit()

    def drop_quota_rows(self, provider: str, account: str, limit_keys: Iterable[str]) -> None:
        """Forget stored limits a newer report of the same source no longer carries."""
        keys = tuple(limit_keys)
        if not keys:
            return
        with self._lock:
            conn = self._connect()
            conn.execute(
                f"DELETE FROM quota_snapshots WHERE provider = ? AND account = ? "
                f"AND limit_key IN ({', '.join('?' for _ in keys)})",
                (provider, account, *keys),
            )
            conn.commit()

    def quota_rows(self, provider: Optional[str] = None) -> list[dict]:
        """The stored quota limits, of one provider or all."""
        where, args = ("WHERE provider = ?", (provider,)) if provider else ("", ())
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT {', '.join(_QUOTA_FIELDS)} FROM quota_snapshots {where} "
                "ORDER BY provider, account, limit_key",
                args,
            ).fetchall()
        return [dict(r) for r in rows]

    def put_quota_read(self, provider: str, account: str, **fields: Any) -> None:
        """Merge ``fields`` (``quota_reads`` columns) into one key's read record."""
        names = [name for name in _QUOTA_READ_FIELDS if name in fields]
        if not names:
            return
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO quota_reads (provider, account) VALUES (?, ?)", (provider, account),
            )
            conn.execute(
                f"UPDATE quota_reads SET {', '.join(f'{n} = ?' for n in names)} "
                "WHERE provider = ? AND account = ?",
                (*(fields[n] for n in names), provider, account),
            )
            conn.commit()

    def quota_reads(self, provider: str, account: Optional[str] = None) -> list[dict]:
        """The read records of a provider's keys (or of one key)."""
        where, args = "provider = ?", [provider]
        if account is not None:
            where += " AND account = ?"
            args.append(account)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT provider, account, {', '.join(_QUOTA_READ_FIELDS)} FROM quota_reads "
                f"WHERE {where} ORDER BY account",
                tuple(args),
            ).fetchall()
        return [dict(r) for r in rows]

    def quota_samples(
        self, *, provider: str, since: float, account: Optional[str] = None,
    ) -> list[dict]:
        """A provider's quota samples since ``since``, oldest first."""
        where, args = "provider = ? AND ts >= ?", [provider, since]
        if account is not None:
            where += " AND account = ?"
            args.append(account)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT provider, account, limit_key, ts, remaining, used_fraction "
                f"FROM quota_samples WHERE {where} ORDER BY ts",
                tuple(args),
            ).fetchall()
        return [dict(r) for r in rows]

    def provider_days(self, *, since: float, until: Optional[float] = None) -> list[dict]:
        """Spend and tokens per provider and UTC day since ``since``.

        Tokens are summed from their parts (input, output, cache read, cache
        write), not from ``total_tokens``: rows written before the recorder
        counted cached tokens in the total left them out of it.
        """
        where, args = "ts >= ?", [since]
        if until is not None:
            where += " AND ts < ?"
            args.append(until)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT provider, CAST(ts / 86400 AS INTEGER) AS day, COUNT(*) AS calls, "
                "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                "SUM(cache_read_tokens) AS cache_read_tokens, "
                "SUM(cache_write_tokens) AS cache_write_tokens, SUM(cost_total) AS cost, "
                "SUM(CASE WHEN COALESCE(cost_source, 'unknown') = 'unknown' THEN 1 ELSE 0 END) "
                "AS unpriced_calls "
                f"FROM usage_events WHERE {where} GROUP BY provider, day ORDER BY provider, day",
                tuple(args),
            ).fetchall()
        return [dict(r) for r in rows]

    def provider_models(self, *, since: float) -> list[dict]:
        """Calls per provider and model since ``since``, most used first."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT provider, model_id, COUNT(*) AS calls, MAX(ts) AS last_ts "
                "FROM usage_events WHERE ts >= ? GROUP BY provider, model_id "
                "ORDER BY calls DESC, last_ts DESC",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def session_activity(self, *, since: float) -> list[dict]:
        """Provider calls since ``since`` per session and model: how many,
        and when the latest one was recorded. Newest activity first."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT session_id, parent_session_id, provider, model_id, "
                "COUNT(*) AS events, MAX(ts) AS last_ts FROM usage_events "
                "WHERE ts >= ? GROUP BY session_id, parent_session_id, provider, model_id "
                "ORDER BY last_ts DESC",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def job_usage(self, job_id: str) -> AggregateRow:
        return self.query(filters={"job_id": job_id})[0]

    def job_resource_usage(self, job_id: str) -> dict:
        """Read the job resource facts from one SQLite result set."""
        with self._lock:
            rows = self._connect().execute(
                """SELECT total_tokens, cost_total, cost_source
                   FROM usage_events WHERE job_id = ?""",
                (job_id,),
            ).fetchall()
        return {
            "total_tokens": sum(int(row["total_tokens"] or 0) for row in rows),
            "cost_values": [row["cost_total"] or 0 for row in rows],
            "events": len(rows),
            "unknown_cost_events": sum(
                (row["cost_source"] or "unknown") == "unknown" for row in rows
            ),
        }

    def resource_counts(self, session_id: str, job_id: str) -> dict:
        run_tables = self.run_tables()
        tables = run_tables.handle
        run_tables.ensure_session(session_id)
        self.ensure_job_session(tables, job_id)
        with run_tables.lock:
            admissions = tables.rows("job_admissions", session_id=session_id)
            row = tables.first("job_admissions", job_id=job_id)
            queue_position = None
            if row is not None and row["state"] == "queued":
                seat = row["admitted_seq"]
                queue_position = tables.count(
                    "job_admissions", state="queued", where=lambda item: item["admitted_seq"] <= seat,
                )
            reservations = tables.rows("usage_reservations", job_id=job_id, state=("reserved", "started"))
        return {
            "resource_state": (
                row["queue_state"] if row is not None and row["state"] == "queued"
                else row["state"] if row is not None else "untracked"
            ),
            "resume_command_id": row["resume_command_id"] if row is not None else None,
            "session_live": {
                "used": sum(item["state"] in ("live", "stopping") for item in admissions), "limit": None,
            },
            "session_queued": {
                "used": sum(item["state"] in ("preparing", "queued") for item in admissions), "limit": None,
            },
            "session_jobs": {"used": len(admissions), "limit": None},
            "queue_position": queue_position,
            "reserved_tokens": sum(int(item["reserved_tokens"] or 0) for item in reservations),
            "reserved_cost_microusd": sum(int(item["reserved_cost_microusd"] or 0) for item in reservations),
        }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._conn_pid = None
            self._run_tables = None
        with self._reader_lock:
            if self._reader is not None:
                self._reader.close()
                self._reader = None
                self._reader_pid = None


# Process-wide default ledger. Lazily connects on first append/query.
default_ledger = UsageLedger()
atexit.register(default_ledger.close)


__all__ = ["UsageLedger", "AggregateRow", "default_ledger"]
