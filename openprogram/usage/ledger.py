"""UsageLedger — append-only SQLite store of UsageEvents.

One global file (``~/.openprogram/usage.db``, profile-aware) with a single
``usage_events`` table. Indexed for the queries the panels need: by time,
by model+time, by session, by kind+time. WAL mode so the @agentic_function
subprocesses can append concurrently with the main worker.

Append is idempotent on ``event_id`` (INSERT OR IGNORE) so a retried write
never double-counts. ``query()`` does the grouping/time-bucketing in SQL.

The class is the default backend behind a thin interface (append/query);
a future JSONL or remote backend can implement the same two methods.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .event import UsageEvent

_COLUMNS = [
    "event_id", "ts", "session_id", "parent_session_id", "agent_id",
    "call_kind", "call_label", "origin_pid", "provider", "api", "model_id",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "cost_total", "cost_input", "cost_output",
    "cost_cache_read", "cost_cache_write", "cost_source", "token_source",
    "schema_version", "task_id", "budget_scope_id", "reservation_id",
]

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
    ,task_id         TEXT
    ,budget_scope_id TEXT
    ,reservation_id  TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_ts       ON usage_events(ts);
CREATE INDEX IF NOT EXISTS ix_usage_model_ts ON usage_events(model_id, ts);
CREATE INDEX IF NOT EXISTS ix_usage_session  ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS ix_usage_kind_ts  ON usage_events(call_kind, ts);
CREATE INDEX IF NOT EXISTS ix_usage_task     ON usage_events(task_id);

CREATE TABLE IF NOT EXISTS task_admissions (
    admission_id TEXT PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    parent_task_id TEXT,
    caller_turn_id TEXT,
    creates_agent INTEGER NOT NULL CHECK (creates_agent IN (0, 1)),
    request_fingerprint TEXT NOT NULL,
    budget_scope_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('preparing','queued','live','stopping','released')),
    admitted_seq INTEGER NOT NULL,
    owner_instance_id TEXT,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    last_activity_at REAL,
    released_at REAL,
    reason_code TEXT
);
CREATE INDEX IF NOT EXISTS ix_admissions_session_state
    ON task_admissions(session_id, state, admitted_seq);

CREATE TABLE IF NOT EXISTS budget_scopes (
    budget_scope_id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('session','task')),
    session_id TEXT NOT NULL,
    task_id TEXT UNIQUE,
    parent_scope_id TEXT,
    max_total_tokens INTEGER,
    max_cost_microusd INTEGER,
    max_runtime_seconds INTEGER,
    idle_timeout_seconds INTEGER,
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_budget_session_scope
    ON budget_scopes(session_id) WHERE scope_kind = 'session';

CREATE TABLE IF NOT EXISTS usage_reservations (
    reservation_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    budget_scope_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('token','cost')),
    state TEXT NOT NULL CHECK (state IN ('reserved','started','settled','released')),
    reserved_tokens INTEGER,
    reserved_cost_microusd INTEGER,
    request_started_at REAL,
    settled_event_id TEXT,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS ix_reservations_scope_state
    ON usage_reservations(budget_scope_id, state);
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


class UsageLedger:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._explicit_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_pid: Optional[int] = None

    # connection (per-process; reopened after fork)

    def _path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        from openprogram.paths import get_usage_db_path
        return get_usage_db_path()

    def _connect(self) -> sqlite3.Connection:
        import os
        # A connection can't cross a fork; reopen if the pid changed so a
        # @agentic_function subprocess gets its own handle to the shared db.
        if self._conn is not None and self._conn_pid == os.getpid():
            return self._conn
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate_usage_columns(conn)
        conn.executescript(_SCHEMA)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn_pid = os.getpid()
        return conn

    @staticmethod
    def _migrate_usage_columns(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(usage_events)")
            }
            for name in ("task_id", "budget_scope_id", "reservation_id"):
                if existing and name not in existing:
                    conn.execute(f"ALTER TABLE usage_events ADD COLUMN {name} TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def connection(self) -> sqlite3.Connection:
        """Return the process-local connection for governance transactions."""
        with self._lock:
            return self._connect()

    @contextmanager
    def immediate(self):
        """Serialize one short cross-process accounting transaction."""
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
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

    def query(
        self,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
        group_by: Optional[list[str]] = None,
        filters: Optional[dict] = None,
    ) -> list[AggregateRow]:
        """Aggregate events. ``group_by`` is a list of field names from
        ``_GROUP_EXPR`` (``day``/``hour`` are time buckets). ``filters`` maps
        an equality column to a value. Unknown group_by tokens are ignored."""
        group_by = [g for g in (group_by or []) if g in _GROUP_EXPR]
        where, params = [], []
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
            "SUM(total_tokens) AS total_tokens, "
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

    def task_usage(self, task_id: str) -> AggregateRow:
        return self.query(filters={"task_id": task_id})[0]

    def resource_counts(self, session_id: str, task_id: str) -> dict:
        with self._lock:
            conn = self._connect()
            counts = conn.execute(
                """SELECT
                    SUM(CASE WHEN state IN ('live','stopping') THEN 1 ELSE 0 END),
                    SUM(CASE WHEN state IN ('preparing','queued') THEN 1 ELSE 0 END),
                    COUNT(*)
                   FROM task_admissions WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            row = conn.execute(
                "SELECT state, admitted_seq FROM task_admissions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            queue_position = None
            if row is not None and row["state"] == "queued":
                queue_position = conn.execute(
                    """SELECT COUNT(*) FROM task_admissions
                       WHERE state = 'queued' AND admitted_seq <= ?""",
                    (row["admitted_seq"],),
                ).fetchone()[0]
            reservations = conn.execute(
                """SELECT COALESCE(SUM(reserved_tokens), 0),
                          COALESCE(SUM(reserved_cost_microusd), 0)
                   FROM usage_reservations
                   WHERE task_id = ? AND state IN ('reserved','started')""",
                (task_id,),
            ).fetchone()
        return {
            "resource_state": row["state"] if row is not None else "untracked",
            "session_live": {"used": int(counts[0] or 0), "limit": None},
            "session_queued": {"used": int(counts[1] or 0), "limit": None},
            "session_tasks": {"used": int(counts[2] or 0), "limit": None},
            "queue_position": queue_position,
            "reserved_tokens": int(reservations[0] or 0),
            "reserved_cost_microusd": int(reservations[1] or 0),
        }

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._conn_pid = None


# Process-wide default ledger. Lazily connects on first append/query.
default_ledger = UsageLedger()


__all__ = ["UsageLedger", "AggregateRow", "default_ledger"]
