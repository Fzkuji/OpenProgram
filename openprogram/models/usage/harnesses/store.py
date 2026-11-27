"""The index of third-party harnesses' calls and sessions: ``<state>/harnesses.db``.

Kept apart from ``usage.db`` so a third-party harness's cost never reaches
OpenProgram's own budgets. A call's key is global across files, so a line
copied into a resumed or forked session counts once: copies keep the
largest value of each token kind and the earliest time.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .files import FileMark

RETAIN_S = 92 * 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, agent TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    offset INTEGER, guard TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS calls (
    agent TEXT NOT NULL, key TEXT NOT NULL, session TEXT, ts REAL NOT NULL,
    model TEXT NOT NULL, speed TEXT NOT NULL DEFAULT '', client TEXT,
    input INTEGER NOT NULL DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0, cache_write_1h INTEGER NOT NULL DEFAULT 0,
    output INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent, key));
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS calls_session ON calls(agent, session);
CREATE TABLE IF NOT EXISTS sessions (
    agent TEXT NOT NULL, id TEXT NOT NULL, name TEXT, first_prompt TEXT,
    cwd TEXT, branch TEXT, client TEXT, path TEXT,
    PRIMARY KEY (agent, id));
CREATE TABLE IF NOT EXISTS quota (
    agent TEXT PRIMARY KEY, observed_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT);
"""

SCHEMA_VERSION = "1"


@dataclass
class Call:
    key: str
    session: Optional[str]
    ts: float
    model: str
    speed: str = ""
    client: Optional[str] = None
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    output: int = 0


@dataclass
class Batch:
    """What one file's new lines added, written in one transaction."""

    agent: str
    calls: list[Call] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    quota: Optional[tuple[float, dict]] = None

    def session(self, session_id: str, **fields: Any) -> None:
        """Merge fields into a session: ``name``, ``branch`` and ``path``
        take the newest value; ``first_prompt``, ``cwd`` and ``client`` the first."""
        if not session_id:
            return
        row = self.sessions.setdefault(session_id, {})
        for key, value in fields.items():
            if value in (None, ""):
                continue
            if key in ("first_prompt", "cwd", "client") and row.get(key):
                continue
            row[key] = value

    def observe_quota(self, observed_at: float, data: dict) -> None:
        if self.quota is None or observed_at >= self.quota[0]:
            self.quota = (observed_at, data)


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._init_lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if not self._ready:
            with self._init_lock:
                if not self._ready:
                    conn.executescript(_SCHEMA)
                    row = conn.execute("SELECT value FROM store_meta WHERE key='schema'").fetchone()
                    if row is None or row[0] != SCHEMA_VERSION:
                        # A reader that changed what it counts reads every file again.
                        conn.executescript("DELETE FROM files; DELETE FROM calls; DELETE FROM sessions; DELETE FROM quota;")
                        conn.execute("INSERT OR REPLACE INTO store_meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
                        conn.commit()
                    self._ready = True
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ── files ────────────────────────────────────────────────────────────

    def marks(self, conn: sqlite3.Connection, agent: str) -> dict[str, tuple[FileMark, dict]]:
        out = {}
        for r in conn.execute("SELECT path, size, mtime_ns, offset, guard, state FROM files WHERE agent=?", (agent,)):
            out[r["path"]] = (FileMark(r["size"], r["mtime_ns"], r["offset"], r["guard"] or ""),
                              json.loads(r["state"] or "{}"))
        return out

    def apply(self, conn: sqlite3.Connection, batch: Batch, path: str, mark: FileMark, state: dict) -> None:
        with conn:
            conn.executemany(
                """INSERT INTO calls (agent, key, session, ts, model, speed, client, input,
                       cache_read, cache_write, cache_write_1h, output)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(agent, key) DO UPDATE SET
                       ts = min(ts, excluded.ts),
                       input = max(input, excluded.input),
                       cache_read = max(cache_read, excluded.cache_read),
                       cache_write = max(cache_write, excluded.cache_write),
                       cache_write_1h = max(cache_write_1h, excluded.cache_write_1h),
                       output = max(output, excluded.output)""",
                [(batch.agent, c.key, c.session, c.ts, c.model, c.speed, c.client, c.input,
                  c.cache_read, c.cache_write, c.cache_write_1h, c.output) for c in batch.calls],
            )
            for sid, f in batch.sessions.items():
                conn.execute(
                    """INSERT INTO sessions (agent, id, name, first_prompt, cwd, branch, client, path)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(agent, id) DO UPDATE SET
                           name = coalesce(excluded.name, name),
                           first_prompt = coalesce(first_prompt, excluded.first_prompt),
                           cwd = coalesce(cwd, excluded.cwd),
                           branch = coalesce(excluded.branch, branch),
                           client = coalesce(client, excluded.client),
                           path = coalesce(excluded.path, path)""",
                    (batch.agent, sid, f.get("name"), f.get("first_prompt"), f.get("cwd"),
                     f.get("branch"), f.get("client"), f.get("path")),
                )
            if batch.quota is not None:
                observed_at, data = batch.quota
                self._write_quota(conn, batch.agent, observed_at, data)
            conn.execute(
                "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
                (path, batch.agent, mark.size, mark.mtime_ns, mark.offset, mark.guard, json.dumps(state)),
            )

    def record_quota(self, agent: str, observed_at: float, data: dict) -> None:
        """Keep a harness's plan limits as its own program reported them
        live; a rollout file's reading is the other source, and the newer
        of the two stays."""
        with self.connection() as conn, conn:
            self._write_quota(conn, agent, observed_at, data)

    @staticmethod
    def _write_quota(conn: sqlite3.Connection, agent: str, observed_at: float, data: dict) -> None:
        conn.execute(
            """INSERT INTO quota VALUES (?,?,?) ON CONFLICT(agent) DO UPDATE SET
                   observed_at = excluded.observed_at, data = excluded.data
               WHERE excluded.observed_at >= quota.observed_at""",
            (agent, observed_at, json.dumps(data)),
        )

    def set_names(self, conn: sqlite3.Connection, agent: str, names: dict[str, str]) -> None:
        with conn:
            conn.executemany(
                """INSERT INTO sessions (agent, id, name) VALUES (?,?,?)
                   ON CONFLICT(agent, id) DO UPDATE SET name = excluded.name""",
                [(agent, sid, name) for sid, name in names.items()],
            )

    def prune(self, conn: sqlite3.Connection, now: Optional[float] = None) -> None:
        cutoff = (now or time.time()) - RETAIN_S
        with conn:
            conn.execute("DELETE FROM calls WHERE ts < ?", (cutoff,))

    def meta(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM store_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        with conn:
            conn.execute("INSERT OR REPLACE INTO store_meta VALUES (?,?)", (key, value))
