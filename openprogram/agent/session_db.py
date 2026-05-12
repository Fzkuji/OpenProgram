"""SQLite-backed session + message store.

One-stop persistence for chat conversations across every transport
(TUI / web / wechat / telegram / discord / slack). Replaces the
per-conv ``meta.json`` + ``messages.json`` layout — that path was
fine at MVP scale but was rewriting the whole messages file every
turn, walking the filesystem on every ``list_sessions`` call,
and offering no way to grep history.

Schema:

  sessions      — one row per conv. Indexed on (agent_id, updated_at)
                  so /resume picker is O(log n). Channel-bound rows
                  carry source/channel/peer fields; local rows leave
                  them NULL.

  messages      — append-only DAG (parent_id → message). Cheap to
                  add a row, indexed on (session_id, timestamp) for
                  history loads. ON DELETE CASCADE so dropping a
                  session sweeps its messages.

  messages_fts  — FTS5 mirror of messages.content. Triggers keep it
                  in sync; ``search_messages`` queries it for full-
                  text recall (e.g. /search 北京天气).

Concurrency: WAL mode + ``timeout=15`` so multiple processes
(channel workers, webui, future replicas) can read while one writes
without "database is locked" errors. WAL also gives us crash safety
without manual fsync.

The ``context_tree`` and ``extra_meta`` columns are JSON text — both
are sparse / structurally varied (different webui execution states,
per-channel custom fields) and SQLite doesn't have a column type
for them. Read with ``json.loads(row["extra_meta"] or "{}")``.
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypeVar

_T = TypeVar("_T")


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  title TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  source TEXT,
  channel TEXT,
  account_id TEXT,
  peer_kind TEXT,
  peer_id TEXT,
  peer_display TEXT,
  head_id TEXT,
  provider_name TEXT,
  model TEXT,
  context_tree TEXT,
  extra_meta TEXT,
  last_prompt_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_updated
  ON sessions(agent_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_source
  ON sessions(source);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  parent_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  timestamp REAL NOT NULL,
  source TEXT,
  peer_display TEXT,
  peer_id TEXT,
  display TEXT,
  function TEXT,
  extra TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  token_source TEXT,
  token_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session_ts
  ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_parent
  ON messages(parent_id);

-- Named branches over the message DAG. A "branch" is identified by
-- the head_msg_id at its tip; the linear chain back to root via
-- parent_id is the branch's commit history. (head_msg_id, session_id)
-- is unique — at most one name per branch tip per session.
CREATE TABLE IF NOT EXISTS branches (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  head_msg_id TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (session_id, head_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_branches_session
  ON branches(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  session_id UNINDEXED,
  content=messages,
  content_rowid=rowid
);
"""

# FTS5 sync triggers — separate from CREATE TABLE since the trigger
# body references the messages table that must already exist.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content, session_id)
    VALUES (new.rowid, new.content, new.session_id);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content, session_id)
    VALUES ('delete', old.rowid, old.content, old.session_id);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content, session_id)
    VALUES ('delete', old.rowid, old.content, old.session_id);
  INSERT INTO messages_fts(rowid, content, session_id)
    VALUES (new.rowid, new.content, new.session_id);
END;
"""


# Columns we model explicitly on `sessions` — anything else lands in
# extra_meta JSON. Used by create/update to pick the right path for
# each field the caller passes in.
_SESSION_COLS = {
    "id", "agent_id", "title", "created_at", "updated_at",
    "source", "channel", "account_id", "peer_kind", "peer_id",
    "peer_display", "head_id", "provider_name", "model",
    "context_tree", "extra_meta", "last_prompt_tokens",
}

_MESSAGE_COLS = {
    "id", "session_id", "parent_id", "role", "content", "timestamp",
    "source", "peer_display", "peer_id", "display", "function", "extra",
    # Token accounting (added 2026-05). Filled by append_message via
    # token_counter; provider_usage on assistant messages overrides the
    # heuristic. NULL for legacy rows — backfill scans on demand.
    "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens",
    "token_source", "token_model",
}


def _default_db_path() -> Path:
    from openprogram.paths import get_state_dir
    return get_state_dir() / "sessions.sqlite"


class SessionDB:
    # ── Write-contention tuning (learned from hermes-agent) ──
    # When multiple OS processes share one sqlite file (gunicorn web
    # workers + channels worker + tui), SQLite's built-in busy handler
    # uses a deterministic backoff that creates convoy-style stalls
    # under contention. Short connection timeout + application-level
    # retry with random jitter staggers competing writers naturally.
    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020
    _WRITE_RETRY_MAX_S = 0.150
    # Periodic best-effort PASSIVE checkpoint — keeps WAL from growing
    # unbounded on long-lived workers.
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: webui's asyncio loop and channel
        # threads share the connection. The write lock below serializes
        # in-process writes; the BEGIN IMMEDIATE + retry loop in
        # _execute_write handles cross-process WAL contention.
        self.conn = sqlite3.connect(
            self.db_path,
            # Short timeout — we retry at the application layer with
            # jitter so SQLite's built-in convoy backoff doesn't kick in.
            timeout=1.0,
            # None = manual transaction mode; lets us BEGIN IMMEDIATE
            # explicitly to grab the WAL write lock at txn start, not
            # at first write (which surfaces contention sooner).
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._write_lock = threading.Lock()
        self._write_count = 0
        self._migrate()

    def _migrate(self) -> None:
        # Schema + triggers live outside _execute_write because
        # CREATE statements implicitly commit and don't play well
        # with our explicit BEGIN IMMEDIATE.
        with self._write_lock:
            self.conn.executescript(SCHEMA)
            self.conn.executescript(TRIGGERS)
            # Idempotent ADD COLUMN for upgrades from pre-token-stats
            # schema. SQLite has no IF NOT EXISTS on ALTER, so we read
            # the current column set and only add the missing ones.
            try:
                cur = self.conn.execute("PRAGMA table_info(messages)")
                existing_cols = {r[1] for r in cur.fetchall()}
            except Exception:
                existing_cols = set()
            for col, decl in (
                ("input_tokens",       "INTEGER"),
                ("output_tokens",      "INTEGER"),
                ("cache_read_tokens",  "INTEGER"),
                ("cache_write_tokens", "INTEGER"),
                ("token_source",       "TEXT"),
                ("token_model",        "TEXT"),
            ):
                if col not in existing_cols:
                    try:
                        self.conn.execute(
                            f"ALTER TABLE messages ADD COLUMN {col} {decl}"
                        )
                    except sqlite3.OperationalError:
                        # Race with another worker doing the same ALTER.
                        pass

    def _execute_write(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run *fn(conn)* inside a BEGIN IMMEDIATE transaction with
        jitter retry on lock/busy errors.

        Why this helper:
          - BEGIN IMMEDIATE acquires the write lock at txn start, so
            contention surfaces here instead of at COMMIT (where
            partial writes complicate retry).
          - Random backoff (20–150ms) staggers competing writers and
            avoids the deterministic-backoff convoy that bites
            multi-process deployments.
          - Periodic PASSIVE checkpoint keeps the WAL bounded for
            long-running workers.

        ``fn`` receives the connection and is expected to perform its
        own INSERT/UPDATE/DELETE statements. Do NOT call ``commit()``
        inside fn — this helper commits on success and rolls back on
        exception.
        """
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._write_lock:
                    self.conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self.conn)
                        self.conn.execute("COMMIT")
                    except BaseException:
                        try:
                            self.conn.execute("ROLLBACK")
                        except Exception:
                            pass
                        raise
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" in msg or "busy" in msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        time.sleep(random.uniform(
                            self._WRITE_RETRY_MIN_S,
                            self._WRITE_RETRY_MAX_S,
                        ))
                        continue
                raise
        raise last_err or sqlite3.OperationalError("write retries exhausted")

    def _try_wal_checkpoint(self) -> None:
        try:
            with self._write_lock:
                self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    # -- session CRUD ------------------------------------------------------

    def create_session(self, session_id: str, agent_id: str,
                       **fields: Any) -> None:
        """Insert a new session. Caller controls created_at / updated_at
        if they want; otherwise both default to now()."""
        now = time.time()
        row: dict[str, Any] = {
            "id": session_id,
            "agent_id": agent_id,
            "created_at": fields.pop("created_at", now),
            "updated_at": fields.pop("updated_at", now),
        }
        extra: dict[str, Any] = {}
        for k, v in fields.items():
            if k in _SESSION_COLS:
                row[k] = v
            else:
                extra[k] = v
        if extra:
            existing_extra = json.loads(row.get("extra_meta") or "{}")
            existing_extra.update(extra)
            row["extra_meta"] = json.dumps(existing_extra, default=str)
        if "context_tree" in row and not isinstance(row["context_tree"], str):
            row["context_tree"] = json.dumps(row["context_tree"], default=str)

        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        sql = (f"INSERT OR REPLACE INTO sessions ({','.join(cols)}) "
               f"VALUES ({placeholders})")
        params = [row[c] for c in cols]
        self._execute_write(lambda conn: conn.execute(sql, params))

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        # Pull current extra_meta so unknown keys merge instead of replace.
        cur = self.get_session(session_id)
        if cur is None:
            # Caller asked to update a missing session — be permissive,
            # create a stub so the channel path's "ingest into possibly
            # missing session" works without ordering ceremony.
            self.create_session(session_id, fields.pop("agent_id", "main"),
                                **fields)
            return
        sets: dict[str, Any] = {}
        # Copy — cur["extra_meta"] is a live dict reference; mutating
        # it would also mutate the comparison target on the next line
        # and the change-detection always returns equal.
        extra = dict(cur.get("extra_meta") or {})
        original_extra = dict(extra)
        for k, v in fields.items():
            if k in _SESSION_COLS:
                sets[k] = v
            else:
                extra[k] = v
        if extra != original_extra:
            sets["extra_meta"] = json.dumps(extra, default=str)
        if "context_tree" in sets and not isinstance(sets["context_tree"], str):
            sets["context_tree"] = json.dumps(sets["context_tree"], default=str)
        sets.setdefault("updated_at", time.time())
        cols = list(sets.keys())
        sql = (f"UPDATE sessions SET {','.join(c + '=?' for c in cols)} "
               f"WHERE id=?")
        params = [sets[c] for c in cols] + [session_id]
        self._execute_write(lambda conn: conn.execute(sql, params))

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,))
        row = cur.fetchone()
        return _row_to_session(row) if row else None

    def list_sessions(self, *, agent_id: Optional[str] = None,
                      source: Optional[str] = None,
                      limit: int = 200,
                      offset: int = 0) -> list[dict[str, Any]]:
        clauses, args = [], []
        if agent_id is not None:
            clauses.append("agent_id=?")
            args.append(agent_id)
        if source is not None:
            clauses.append("source=?")
            args.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self.conn.execute(
            f"SELECT * FROM sessions{where} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
        return [_row_to_session(r) for r in cur.fetchall()]

    def count_sessions(self, *, agent_id: Optional[str] = None,
                       source: Optional[str] = None) -> int:
        clauses, args = [], []
        if agent_id is not None:
            clauses.append("agent_id=?")
            args.append(agent_id)
        if source is not None:
            clauses.append("source=?")
            args.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self.conn.execute(f"SELECT COUNT(*) FROM sessions{where}", args)
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def delete_session(self, session_id: str) -> None:
        self._execute_write(
            lambda conn: conn.execute("DELETE FROM sessions WHERE id=?",
                                       (session_id,))
        )

    # -- message CRUD ------------------------------------------------------

    def append_message(self, session_id: str, msg: dict[str, Any]) -> None:
        row: dict[str, Any] = {
            "session_id": session_id,
            "timestamp": msg.get("timestamp") or time.time(),
        }
        extra: dict[str, Any] = {}
        for k, v in msg.items():
            if k in _MESSAGE_COLS:
                row[k] = v
            else:
                extra[k] = v
        if extra:
            row["extra"] = json.dumps(extra, default=str)

        # Auto-fill token columns if caller didn't supply them. Caller
        # may override (e.g., backfill scans pass pre-computed values
        # or trust the provider's usage block from an AssistantMessage).
        if "input_tokens" not in row or row.get("input_tokens") is None:
            try:
                from openprogram.providers._shared.token_counter import count_tokens
                # Best-effort: look up the model the assistant message
                # came from. UserMessage has no model_id, so tiktoken
                # falls back to heuristic — acceptable.
                model_obj = None
                model_id = msg.get("model") or extra.get("model")
                provider_name = msg.get("provider") or extra.get("provider")
                if model_id:
                    try:
                        from openprogram.providers.models_generated import MODELS
                        key = (
                            f"{provider_name}/{model_id}" if provider_name else None
                        )
                        model_obj = (MODELS.get(key) if key else None) or \
                            MODELS.get(model_id)
                        if model_obj is None:
                            for v in MODELS.values():
                                if v.id == model_id:
                                    model_obj = v
                                    break
                    except Exception:
                        model_obj = None
                tc = count_tokens(msg, model_obj)
                row.setdefault("input_tokens", tc.input)
                row.setdefault("output_tokens", tc.output)
                row.setdefault("cache_read_tokens", tc.cache_read)
                row.setdefault("cache_write_tokens", tc.cache_write)
                row.setdefault("token_source", tc.source)
                if model_obj is not None:
                    row.setdefault("token_model", getattr(model_obj, "id", None))
                elif model_id:
                    row.setdefault("token_model", str(model_id))
            except Exception:
                # Token counting must never block message persistence.
                pass
        # Required columns
        if "id" not in row or "role" not in row or "content" not in row:
            raise ValueError(
                "append_message requires id, role, content; got: "
                + ",".join(sorted(msg.keys())))
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        sql = (f"INSERT OR REPLACE INTO messages ({','.join(cols)}) "
               f"VALUES ({placeholders})")
        params = [row[c] for c in cols]
        ts = row["timestamp"]

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(sql, params)
            # Bump session.updated_at so /resume picker re-sorts.
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (ts, session_id),
            )

        self._execute_write(_do)

    def get_messages(self, session_id: str, *,
                     limit: Optional[int] = None) -> list[dict[str, Any]]:
        sql = ("SELECT * FROM messages WHERE session_id=? "
               "ORDER BY timestamp ASC")
        args: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        cur = self.conn.execute(sql, args)
        return [_row_to_message(r) for r in cur.fetchall()]

    def sessions_with_binding(self, channel: str, account_id: Optional[str]) -> list[str]:
        """Return ids of all sessions currently bound to ``channel`` +
        ``account_id``. Used to enforce single-session ownership of a
        channel account when the user re-binds via the UI."""
        if account_id is None:
            cur = self.conn.execute(
                "SELECT id FROM sessions WHERE channel=? AND account_id IS NULL",
                (channel,),
            )
        else:
            cur = self.conn.execute(
                "SELECT id FROM sessions WHERE channel=? AND account_id=?",
                (channel, account_id),
            )
        return [r[0] for r in cur.fetchall()]

    # -- branches (git-style named heads over the message DAG) ----------

    def list_branches(self, session_id: str) -> list[dict[str, Any]]:
        """Return one row per leaf message in the session — that is,
        every potential branch tip. Rows carry the explicit name from
        the `branches` table if any, otherwise an empty name (callers
        synthesise a default like "main" or the first user-message
        text). Ordered by leaf timestamp ascending so older branches
        come first; the active head walker stays consistent across
        calls."""
        cur = self.conn.execute(
            "SELECT m.id, m.timestamp "
            "FROM messages m "
            "WHERE m.session_id=? AND NOT EXISTS ("
            "  SELECT 1 FROM messages c "
            "  WHERE c.session_id=? AND c.parent_id=m.id"
            ") "
            "ORDER BY m.timestamp ASC",
            (session_id, session_id),
        )
        leaves = [(r[0], r[1]) for r in cur.fetchall()]
        if not leaves:
            return []
        # Pull explicit names in one query.
        cur = self.conn.execute(
            "SELECT head_msg_id, name, created_at FROM branches "
            "WHERE session_id=?",
            (session_id,),
        )
        names = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        out = []
        for mid, ts in leaves:
            n = names.get(mid)
            out.append({
                "head_msg_id": mid,
                "name": n[0] if n else None,
                "created_at": n[1] if n else ts,
                "leaf_timestamp": ts,
            })
        return out

    def set_branch_name(self, session_id: str, head_msg_id: str,
                        name: str) -> None:
        """Upsert a branch name for ``head_msg_id``. Creates the row if
        missing, otherwise overwrites. Caller is responsible for
        validating that the head exists."""
        import time as _t
        def _do(conn):
            conn.execute(
                "INSERT INTO branches(session_id, head_msg_id, name, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, head_msg_id) DO UPDATE SET name=excluded.name",
                (session_id, head_msg_id, name, _t.time()),
            )
            conn.commit()
        self._execute_write(_do)

    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        """Drop the explicit name for a branch. The branch tip itself
        (the message) is untouched — only the named-label is removed."""
        def _do(conn):
            conn.execute(
                "DELETE FROM branches WHERE session_id=? AND head_msg_id=?",
                (session_id, head_msg_id),
            )
            conn.commit()
        self._execute_write(_do)

    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        """Delete the unique tail of a branch ending at head_msg_id.

        Walks parent_id upwards from the leaf, deleting messages that
        have no children left. Stops at the first message with siblings
        (the fork point shared with another branch) — removing it would
        corrupt the other branches. Returns count of deleted messages.
        """
        deleted = 0
        cursor = head_msg_id
        while cursor:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=? AND parent_id=?",
                (session_id, cursor),
            ).fetchone()
            if row and row[0] > 0:
                # Has remaining children → it's a fork point shared with
                # another branch. Stop walking.
                break
            parent_row = self.conn.execute(
                "SELECT parent_id FROM messages WHERE session_id=? AND id=?",
                (session_id, cursor),
            ).fetchone()
            if parent_row is None:
                break
            parent_id = parent_row[0]
            cur_id = cursor
            def _do(conn, mid=cur_id):
                conn.execute(
                    "DELETE FROM branches WHERE session_id=? AND head_msg_id=?",
                    (session_id, mid),
                )
                conn.execute(
                    "DELETE FROM messages WHERE session_id=? AND id=?",
                    (session_id, mid),
                )
                conn.commit()
            self._execute_write(_do)
            deleted += 1
            cursor = parent_id
        return deleted

    def latest_user_text(self, session_id: str) -> Optional[str]:
        """Return the most recent user-message text for ``session_id``,
        or None if the session has no user messages yet. Used by the
        sidebar to show a content preview when the session's title is
        a backend-generated placeholder (e.g. ``WeChat: <id>``)."""
        cur = self.conn.execute(
            "SELECT content FROM messages "
            "WHERE session_id=? AND role='user' "
            "ORDER BY timestamp DESC LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        v = row[0]
        return v if isinstance(v, str) else None

    # -- branching (DAG walking) -----------------------------------------
    #
    # OpenProgram messages form an append-only DAG: every row carries
    # ``parent_id`` (NULL at the root). The "current view" of a session
    # is the linear chain from the session's ``head_id`` walking parent
    # links back to root. New writes append a child of the current
    # head; retry / edit appends a child of an *older* message, which
    # creates a sibling — no rewrite, no delete. The chain still walks
    # cleanly because each leaf's parent path is single-parent.
    #
    # This is the same model Claude Code uses on its JSONL transcripts
    # (``parentUuid`` chain), but in SQLite so we get index-backed walk
    # and FTS for free. Hermes / OpenClaw do session-level fork instead
    # — strictly less expressive than message-level DAG.

    def get_branch(self, session_id: str,
                   head_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Return the linear branch ending at ``head_id`` (or the
        session's current head when omitted), oldest first.

        Walks ``parent_id`` from head back to root via a recursive CTE,
        then returns rows in chronological order. Cheap because of
        ``idx_messages_parent``. Used by every read path that wants the
        "what the user currently sees" slice — webui bootstrap, /resume,
        dispatcher's history load.

        Empty list if ``head_id`` is missing or doesn't belong to the
        session — never raises. The dispatcher needs the empty path to
        bootstrap the very first turn (no head yet) without ceremony.
        """
        if head_id is None:
            sess = self.get_session(session_id)
            if sess is None:
                return []
            head_id = sess.get("head_id")
        if not head_id:
            return []
        cur = self.conn.execute(
            "WITH RECURSIVE branch(id, session_id, parent_id, role, content, "
            "  timestamp, source, peer_display, peer_id, display, function, extra) AS ("
            "  SELECT id, session_id, parent_id, role, content, timestamp, "
            "    source, peer_display, peer_id, display, function, extra "
            "    FROM messages WHERE id=? AND session_id=?"
            "  UNION ALL"
            "  SELECT m.id, m.session_id, m.parent_id, m.role, m.content, m.timestamp, "
            "    m.source, m.peer_display, m.peer_id, m.display, m.function, m.extra "
            "    FROM messages m JOIN branch b ON m.id = b.parent_id "
            "    WHERE m.session_id = ?"
            ") SELECT * FROM branch ORDER BY timestamp ASC",
            (head_id, session_id, session_id),
        )
        return [_row_to_message(r) for r in cur.fetchall()]

    def get_branch_token_stats(self,
                                session_id: str,
                                head_id: Optional[str] = None,
                                model: Any = None) -> dict[str, Any]:
        """Token accounting for one branch.

        Walks the branch (same CTE as get_branch) collecting token
        columns + role + model. Returns:

            {
              "branch": [ {message_id, role, input_tokens, output_tokens,
                           cache_read_tokens, cache_write_tokens, total,
                           token_source, token_model, timestamp}, ... ],
              "naive_sum":            int,  # sum of per-row totals
              "last_assistant_usage": int,  # input + cache_read of newest
                                            #   assistant message — the
                                            #   provider's own ground truth
                                            #   for "context the model just
                                            #   saw on this branch"
              "current_tokens":       int,  # last_assistant_usage if
                                            #   available, else naive_sum
              "context_window":       int,  # from model.context_window
              "pct_used":             float, # current_tokens / window
              "cache_read_total":     int,
              "cache_hit_rate":       float, # cache_read / (input+cache_read)
              "model":                str,
              "source_mix":           dict   # source → count, for
                                            #   precision disclosure
            }

        opencode insight: the newest assistant message's `usage.input`
        already represents the FULL context the model just consumed for
        this branch. Naive per-row summation over-counts (every turn
        re-includes prior context). We surface both numbers so callers
        can pick. Provider_usage rows beat tiktoken rows beat heuristic
        rows — `source_mix` tells the UI how trustworthy the answer is.
        """
        if head_id is None:
            sess = self.get_session(session_id)
            if sess is None:
                return {"branch": [], "naive_sum": 0, "last_assistant_usage": 0,
                        "current_tokens": 0, "context_window": 0, "pct_used": 0.0,
                        "cache_read_total": 0, "cache_hit_rate": 0.0,
                        "model": None, "source_mix": {}}
            head_id = sess.get("head_id")
        if not head_id:
            return {"branch": [], "naive_sum": 0, "last_assistant_usage": 0,
                    "current_tokens": 0, "context_window": 0, "pct_used": 0.0,
                    "cache_read_total": 0, "cache_hit_rate": 0.0,
                    "model": None, "source_mix": {}}

        cur = self.conn.execute(
            "WITH RECURSIVE branch(id, parent_id, role, timestamp, content, "
            "  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
            "  token_source, token_model) AS ("
            "  SELECT id, parent_id, role, timestamp, content, "
            "    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
            "    token_source, token_model "
            "    FROM messages WHERE id=? AND session_id=?"
            "  UNION ALL"
            "  SELECT m.id, m.parent_id, m.role, m.timestamp, m.content, "
            "    m.input_tokens, m.output_tokens, m.cache_read_tokens, m.cache_write_tokens, "
            "    m.token_source, m.token_model "
            "    FROM messages m JOIN branch b ON m.id = b.parent_id "
            "    WHERE m.session_id = ?"
            ") SELECT * FROM branch ORDER BY timestamp ASC",
            (head_id, session_id, session_id),
        )
        rows = cur.fetchall()

        branch: list[dict[str, Any]] = []
        naive_sum = 0
        cache_read_total = 0
        input_total = 0
        last_assistant_usage = 0
        last_model: Optional[str] = None
        source_mix: dict[str, int] = {}
        # Cumulative estimate from message content lengths — fallback when
        # provider usage is missing (early sessions, channel paths that
        # don't record tokens, etc.). ~4 chars per token is the standard
        # rule of thumb across English + code.
        content_estimate = 0

        for r in rows:
            inp = int(r["input_tokens"] or 0)
            out = int(r["output_tokens"] or 0)
            cr  = int(r["cache_read_tokens"]  or 0)
            cw  = int(r["cache_write_tokens"] or 0)
            total = inp + out + cr
            naive_sum += total
            cache_read_total += cr
            input_total += inp
            source = r["token_source"] or "unknown"
            source_mix[source] = source_mix.get(source, 0) + 1
            # Per-message char-based estimate (only counts this message's own
            # content once — unlike input_tokens which already accumulates
            # the prior context). For tracked messages we use the bigger of
            # output_tokens vs estimate (since output is what this message
            # CONTRIBUTES to context for downstream turns).
            content = r["content"] if "content" in r.keys() else None
            char_est = (len(content) // 4) if isinstance(content, str) else 0
            per_msg_contrib = max(out, char_est) if (inp or cr or out) else char_est
            content_estimate += per_msg_contrib
            if r["role"] in ("assistant", "model") and (inp or cr):
                last_assistant_usage = inp + cr
                if r["token_model"]:
                    last_model = r["token_model"]
            branch.append({
                "message_id": r["id"],
                "role": r["role"],
                "input_tokens":  inp,
                "output_tokens": out,
                "cache_read_tokens":  cr,
                "cache_write_tokens": cw,
                "total": total,
                "token_source": source,
                "token_model":  r["token_model"],
                "timestamp": r["timestamp"],
            })

        # current_tokens = best available reading of the context size at HEAD.
        # If provider reported usage on the last assistant turn, trust it —
        # but check it's not absurdly small compared to the content estimate
        # (that means most prior messages had no token recording).
        if last_assistant_usage and last_assistant_usage >= content_estimate * 0.5:
            current_tokens = last_assistant_usage
        else:
            current_tokens = max(last_assistant_usage, content_estimate, naive_sum)
        ctx_window = 0
        model_id = None
        if model is not None:
            ctx_window = int(getattr(model, "context_window", 0) or 0)
            model_id = getattr(model, "id", None)
        if not model_id and last_model:
            model_id = last_model
        pct = (current_tokens / ctx_window) if ctx_window else 0.0
        cache_hit_rate = 0.0
        if input_total + cache_read_total > 0:
            cache_hit_rate = cache_read_total / (input_total + cache_read_total)

        return {
            "branch": branch,
            "naive_sum": naive_sum,
            "last_assistant_usage": last_assistant_usage,
            "content_estimate": content_estimate,
            "current_tokens": current_tokens,
            "context_window": ctx_window,
            "pct_used": pct,
            "cache_read_total": cache_read_total,
            "cache_hit_rate": cache_hit_rate,
            "model": model_id,
            "source_mix": source_mix,
        }

    def set_head(self, session_id: str, head_id: Optional[str]) -> None:
        """Switch a session's head_id. Used by retry / edit / branch
        navigation in the UI. Pass ``None`` to clear (e.g. /clear).

        After ``set_head``, the next ``append_message`` whose parent_id
        is unset will chain off whatever the caller computes from
        ``get_branch`` — typically the new head itself. Note that
        ``append_message`` does NOT auto-update head_id, so callers
        that want "advance head to the new message" must call
        ``set_head`` after ``append_message``.
        """
        self._execute_write(
            lambda conn: conn.execute(
                "UPDATE sessions SET head_id=?, updated_at=? WHERE id=?",
                (head_id, time.time(), session_id),
            )
        )

    def get_descendants(self, session_id: str,
                        root_id: str) -> list[dict[str, Any]]:
        """All messages in the subtree rooted at ``root_id`` (inclusive).
        Used to find sibling branches when the user asks "show me the
        other forks of this turn"."""
        cur = self.conn.execute(
            "WITH RECURSIVE descendants(id, session_id, parent_id, role, content, "
            "  timestamp, source, peer_display, peer_id, display, function, extra) AS ("
            "  SELECT id, session_id, parent_id, role, content, timestamp, "
            "    source, peer_display, peer_id, display, function, extra "
            "    FROM messages WHERE id=? AND session_id=?"
            "  UNION ALL"
            "  SELECT m.id, m.session_id, m.parent_id, m.role, m.content, m.timestamp, "
            "    m.source, m.peer_display, m.peer_id, m.display, m.function, m.extra "
            "    FROM messages m JOIN descendants d ON m.parent_id = d.id "
            "    WHERE m.session_id = ?"
            ") SELECT * FROM descendants ORDER BY timestamp ASC",
            (root_id, session_id, session_id),
        )
        return [_row_to_message(r) for r in cur.fetchall()]

    def get_deepest_leaf(self, session_id: str,
                          root_id: str) -> Optional[str]:
        """Find the leaf (message with no child) under ``root_id`` with
        the latest timestamp. When the user clicks an old message in
        the sidebar, we jump head to the deepest descendant on that
        sub-tree — same UX as ``contextgit/dag.py:deepest_leaf`` but
        in SQL so it's O(branch_size) not O(all messages).

        Returns the leaf's id, or ``root_id`` if it has no descendants,
        or ``None`` if ``root_id`` doesn't exist in this session.
        """
        # First confirm root exists. We need this so callers can
        # distinguish "leaf is root" from "no such root".
        chk = self.conn.execute(
            "SELECT id FROM messages WHERE id=? AND session_id=?",
            (root_id, session_id),
        ).fetchone()
        if chk is None:
            return None
        cur = self.conn.execute(
            "WITH RECURSIVE descendants(id, parent_id, timestamp) AS ("
            "  SELECT id, parent_id, timestamp FROM messages "
            "    WHERE id=? AND session_id=?"
            "  UNION ALL"
            "  SELECT m.id, m.parent_id, m.timestamp FROM messages m "
            "    JOIN descendants d ON m.parent_id = d.id "
            "    WHERE m.session_id = ?"
            ") "
            # Leaves: descendants whose id is no other row's parent_id.
            "SELECT d.id FROM descendants d "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM messages c "
            "  WHERE c.parent_id = d.id AND c.session_id = ?"
            ") ORDER BY d.timestamp DESC LIMIT 1",
            (root_id, session_id, session_id, session_id),
        )
        row = cur.fetchone()
        return row["id"] if row else root_id

    def search_messages(self, query: str, *,
                        agent_id: Optional[str] = None,
                        limit: int = 50) -> list[dict[str, Any]]:
        """FTS5 full-text search across message content. Returns matched
        messages joined with their session info (so /search can show
        which conv each hit came from)."""
        # FTS5 wraps the query in a MATCH clause; quote arbitrary input
        # so it's safe (FTS5 has its own syntax — quoted strings are
        # treated as a phrase search, no operator parsing).
        safe_query = '"' + query.replace('"', '""') + '"'
        if agent_id is not None:
            cur = self.conn.execute(
                "SELECT m.*, s.title AS session_title, s.source AS session_source "
                "FROM messages_fts f "
                "JOIN messages m ON m.rowid = f.rowid "
                "JOIN sessions s ON s.id = m.session_id "
                "WHERE messages_fts MATCH ? AND s.agent_id=? "
                "ORDER BY m.timestamp DESC LIMIT ?",
                (safe_query, agent_id, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT m.*, s.title AS session_title, s.source AS session_source "
                "FROM messages_fts f "
                "JOIN messages m ON m.rowid = f.rowid "
                "JOIN sessions s ON s.id = m.session_id "
                "WHERE messages_fts MATCH ? "
                "ORDER BY m.timestamp DESC LIMIT ?",
                (safe_query, limit),
            )
        out = []
        for r in cur.fetchall():
            d = _row_to_message(r)
            d["session_title"] = r["session_title"]
            d["session_source"] = r["session_source"]
            out.append(d)
        return out

    # -- bulk helpers ------------------------------------------------------

    def append_messages(self, session_id: str,
                        msgs: Iterable[dict[str, Any]]) -> None:
        """Atomic append of a list of messages (single transaction).
        Used during session imports / replays."""
        msgs = list(msgs)
        if not msgs:
            return

        def _do(conn: sqlite3.Connection) -> None:
            for m in msgs:
                row: dict[str, Any] = {
                    "session_id": session_id,
                    "timestamp": m.get("timestamp") or time.time(),
                }
                extra: dict[str, Any] = {}
                for k, v in m.items():
                    if k in _MESSAGE_COLS:
                        row[k] = v
                    else:
                        extra[k] = v
                if extra:
                    row["extra"] = json.dumps(extra, default=str)
                if "id" not in row or "role" not in row or "content" not in row:
                    continue
                cols = list(row.keys())
                conn.execute(
                    f"INSERT OR REPLACE INTO messages ({','.join(cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})",
                    [row[c] for c in cols],
                )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (msgs[-1].get("timestamp") or time.time(), session_id),
            )

        self._execute_write(_do)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# -- row → dict conversion ---------------------------------------------

def _row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    extra = d.pop("extra_meta", None)
    if extra:
        try:
            extra_obj = json.loads(extra)
        except Exception:
            extra_obj = {}
    else:
        extra_obj = {}
    ct = d.get("context_tree")
    if ct:
        try:
            d["context_tree"] = json.loads(ct)
        except Exception:
            pass
    # Hoist extra_meta keys to top level so callers see one flat dict.
    for k, v in extra_obj.items():
        d.setdefault(k, v)
    d["extra_meta"] = extra_obj
    return d


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    extra = d.pop("extra", None)
    if extra:
        try:
            extra_obj = json.loads(extra)
        except Exception:
            extra_obj = {}
        for k, v in extra_obj.items():
            d.setdefault(k, v)
    return d


# -- module-level singleton -------------------------------------------

_default: Optional[SessionDB] = None
_default_lock = threading.Lock()


def default_db() -> SessionDB:
    """Process-wide singleton. Channels worker + webui server share
    this instance; SessionDB itself is thread-safe."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = SessionDB()
    return _default
