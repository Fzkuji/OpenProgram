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
    "schema_version",
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
);
CREATE INDEX IF NOT EXISTS ix_usage_ts       ON usage_events(ts);
CREATE INDEX IF NOT EXISTS ix_usage_model_ts ON usage_events(model_id, ts);
CREATE INDEX IF NOT EXISTS ix_usage_session  ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS ix_usage_kind_ts  ON usage_events(call_kind, ts);
"""

# group_by token → SQL expression. ``day``/``hour`` are time buckets.
_GROUP_EXPR = {
    "model_id": "model_id",
    "provider": "provider",
    "call_kind": "call_kind",
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


class UsageLedger:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._explicit_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_pid: Optional[int] = None

    # ── connection (per-process; reopened after fork) ──

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
        conn.executescript(_SCHEMA)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn_pid = os.getpid()
        return conn

    # ── write ──

    def append(self, event: UsageEvent) -> None:
        row = [getattr(event, c) for c in _COLUMNS]
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = (f"INSERT OR IGNORE INTO usage_events ({','.join(_COLUMNS)}) "
               f"VALUES ({placeholders})")
        with self._lock:
            conn = self._connect()
            conn.execute(sql, row)
            conn.commit()

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

    # ── read ──

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
            ))
        return out

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                self._conn_pid = None


# Process-wide default ledger. Lazily connects on first append/query.
default_ledger = UsageLedger()


__all__ = ["UsageLedger", "AggregateRow", "default_ledger"]
