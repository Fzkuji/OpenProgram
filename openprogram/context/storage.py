"""SQLite persistence for ``context.nodes.Graph``.

One database file holds many sessions. Each row in ``nodes`` belongs to
one session; the flat DAG model (UserMessage / ModelCall / FunctionCall
+ predecessor + reads) maps 1:1 onto two simple tables:

    sessions  — one row per conversation
    nodes     — one row per graph node, type-specific fields in JSON

Plus an optional FTS5 virtual table over node text content for search.

The data model from ``context.nodes`` is preserved exactly. Switching from
the previous file+git backend changes *storage*, not *semantics*.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from openprogram.context.nodes import (
    Call,
    Graph,
    Node,         # alias for Call, kept for compat
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    agent_id      TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    extra_json    TEXT NOT NULL DEFAULT '{}',
    last_node_id  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,
    -- predecessor: conversation edge (user→assistant→user).
    --   Set for user/assistant nodes. Subsequent conv turns chain
    --   through this column.
    -- caller: call edge (assistant→tool→sub-llm).
    --   Set for FunctionCall/sub-call nodes. These are NOT on the
    --   conversation chain and CANNOT be branch tips.
    -- The two are mutually exclusive: each node sets at most one.
    predecessor  TEXT,
    caller       TEXT,
    created_at   REAL NOT NULL,
    seq          INTEGER NOT NULL,
    data_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_session_seq
    ON nodes(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_nodes_predecessor
    ON nodes(predecessor);
-- idx_nodes_caller is created in init_db() after the ALTER TABLE
-- migration so older DBs without the column don't choke here.

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    text,
    session_id UNINDEXED,
    node_id UNINDEXED
);
"""


def init_db(db_path: str | Path) -> None:
    """Create the schema in ``db_path`` if not already present.

    Also runs in-place ALTER TABLE migrations for older DBs that
    predate the ``caller`` column.
    """
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA)
        # In-place migrations for pre-existing DBs.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        if "caller" not in cols:
            conn.execute("ALTER TABLE nodes ADD COLUMN caller TEXT")
        # Index outside the IF so a freshly-created DB also picks it up.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_caller ON nodes(caller)"
        )
        conn.commit()


# ── Node ↔ row serialization ────────────────────────────────────────


def _node_to_data_json(node: Call) -> str:
    """Pack non-column Call fields as JSON. Common fields (id,
    seq, created_at, role) live in their own columns."""
    d = asdict(node)
    for skip in ("id", "seq", "created_at", "role"):
        d.pop(skip, None)
    return json.dumps(d, ensure_ascii=False, default=str)


_LEGACY_TYPE_TO_ROLE = {
    "UserMessage":  ROLE_USER,
    "ModelCall":    ROLE_LLM,
    "FunctionCall": ROLE_CODE,
}


def _row_to_node(row: sqlite3.Row) -> Call:
    """Reconstruct a Call from a nodes row. The ``type`` column carries
    the role string; ``seq`` carries the time-order integer.

    Tolerates legacy data: pre-Call rows used types ``UserMessage`` /
    ``ModelCall`` / ``FunctionCall`` with field names ``content`` /
    ``model`` / ``function_name`` / ``arguments`` / ``result`` /
    ``system_prompt``. We translate them on the way out so old DBs
    still load.
    """
    kwargs = json.loads(row["data_json"])
    # Drop fields that aren't on the Call dataclass.
    kwargs.pop("predecessor", None)
    kwargs.pop("type", None)

    role_or_type = row["type"]
    # Legacy: type column held the dataclass name; translate.
    if role_or_type in _LEGACY_TYPE_TO_ROLE:
        role = _LEGACY_TYPE_TO_ROLE[role_or_type]
        # Migrate legacy data_json field names.
        if "content" in kwargs:
            kwargs.setdefault("output", kwargs.pop("content"))
        if "model" in kwargs:
            kwargs.setdefault("name", kwargs.pop("model"))
        if "function_name" in kwargs:
            kwargs.setdefault("name", kwargs.pop("function_name"))
        if "arguments" in kwargs:
            kwargs.setdefault("input", kwargs.pop("arguments"))
        if "result" in kwargs:
            kwargs.setdefault("output", kwargs.pop("result"))
        if "system_prompt" in kwargs:
            sp = kwargs.pop("system_prompt")
            if sp:
                existing_input = kwargs.get("input")
                if isinstance(existing_input, dict):
                    existing_input.setdefault("system", sp)
                else:
                    kwargs["input"] = {"system": sp}
        if "triggered_by" in kwargs and "called_by" not in kwargs:
            kwargs["called_by"] = kwargs.pop("triggered_by") or ""
        else:
            kwargs.pop("triggered_by", None)
    else:
        role = role_or_type

    kwargs["id"] = row["id"]
    kwargs["created_at"] = row["created_at"]
    kwargs["role"] = role
    kwargs["seq"] = row["seq"]

    # Final defense: drop any unknown kwargs so an unknown legacy
    # field never crashes Call construction.
    valid_fields = {
        "id", "seq", "created_at", "role", "name",
        "input", "output", "called_by", "reads", "metadata",
    }
    kwargs = {k: v for k, v in kwargs.items() if k in valid_fields}

    return Call(**kwargs)


def _node_text_for_fts(node: Call) -> str:
    """Plain-text projection for FTS indexing.

    Every Call gets its ``output`` indexed — that's what's most likely
    searched for ("find the message where I asked about X").
    Type-specific extras:
      - llm: include system prompt (input.system) if any
      - code: include the arguments dict as JSON
      - user: output is the message text itself; nothing else to add
    """
    parts: list[str] = []
    if node.output is not None:
        parts.append(str(node.output))
    if node.name:
        parts.append(str(node.name))
    if node.is_llm() and isinstance(node.input, dict):
        sys = node.input.get("system")
        if sys:
            parts.append(str(sys))
    elif node.is_code() and node.input is not None:
        try:
            parts.append(json.dumps(node.input, default=str))
        except (TypeError, ValueError):
            parts.append(repr(node.input))
    return "\n".join(p for p in parts if p)


# ── GraphStore: one instance per session ────────────────────────────


class GraphStore:
    """Persistence + append-only writer for one session inside a SQLite DB.

    The store does not hold a SQLite connection across method calls; it
    opens one per operation. That keeps it threadsafe and concurrency-
    safe at minor cost.

    To create or open the underlying DB, call ``init_db(db_path)`` once
    per process.
    """

    def __init__(self, db_path: str | Path, session_id: str):
        self.db_path = Path(db_path).expanduser()
        self.session_id = session_id

    # ── Connection helper ──────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── Session-level helpers ──────────────────────────────────────

    def session_exists(self) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?",
                (self.session_id,),
            )
            return cur.fetchone() is not None

    def create_session_row(
        self,
        *,
        title: str = "",
        model: str = "",
        agent_id: str = "",
        source: str = "",
        extra: Optional[dict] = None,
    ) -> None:
        """Insert a sessions row for this store's session_id."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, title, created_at, updated_at, model, agent_id, source, extra_json, last_node_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    self.session_id,
                    title,
                    now,
                    now,
                    model,
                    agent_id,
                    source,
                    json.dumps(extra or {}, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()

    def update_session_row(self, **fields: Any) -> None:
        """Update one or more sessions columns. Always bumps updated_at."""
        cols, values = [], []
        for k, v in fields.items():
            if k == "extra":
                cols.append("extra_json = ?")
                values.append(json.dumps(v, ensure_ascii=False, default=str))
            elif k in ("title", "model", "agent_id", "source"):
                cols.append(f"{k} = ?")
                values.append(v)
        cols.append("updated_at = ?")
        values.append(time.time())
        values.append(self.session_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(cols)} WHERE id = ?",
                values,
            )
            conn.commit()

    # ── Graph load ─────────────────────────────────────────────────

    def load(self) -> Graph:
        """Reconstruct the Graph from rows in ``nodes`` for this session.
        Nodes come back sorted by seq."""
        g = Graph()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, type, seq, created_at, data_json
                   FROM nodes WHERE session_id = ? ORDER BY seq""",
                (self.session_id,),
            ).fetchall()
            for row in rows:
                node = _row_to_node(row)
                g.nodes[node.id] = node
                if node.seq >= g._next_seq:
                    g._next_seq = node.seq + 1
        return g

    # ── Append a new node ──────────────────────────────────────────

    def append(self, node: Call) -> None:
        """Persist a node. Assigns ``node.seq`` if it's unset (< 0).
        Refuses to insert a node whose id is already present."""
        with self._connect() as conn:
            # Assign seq if the caller hasn't already. Monotonic per session.
            if node.seq < 0:
                cur = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM nodes "
                    "WHERE session_id = ?",
                    (self.session_id,),
                )
                node.seq = cur.fetchone()[0]

            data_json = _node_to_data_json(node)
            text = _node_text_for_fts(node)

            # Two independent parent edges, mutually exclusive:
            #   - caller:      this node is a sub-call (tool / FunctionCall
            #                   / sub-LLM) of another node. Set this and
            #                   leave predecessor NULL — sub-calls are not
            #                   on the conversation chain and cannot be
            #                   branch tips.
            #   - predecessor: this node is the next conversation turn
            #                   after another conversation node.
            # Decision: if Call.called_by is set, OR the legacy
            # metadata.called_by is set (older code path), treat this as
            # a sub-call. Otherwise it's a conversation node and we keep
            # metadata.parent_id as the conv edge.
            legacy_caller = (node.metadata or {}).get("called_by") or None
            caller_for_sql = (node.called_by or legacy_caller) or None
            if caller_for_sql:
                predecessor_for_sql = None
            else:
                predecessor_for_sql = (
                    (node.metadata or {}).get("parent_id") or None
                )
            try:
                conn.execute(
                    """INSERT INTO nodes
                       (id, session_id, type, predecessor, caller,
                        created_at, seq, data_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        node.id,
                        self.session_id,
                        node.role,
                        predecessor_for_sql,
                        caller_for_sql,
                        node.created_at,
                        node.seq,
                        data_json,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(
                    f"Node {node.id!r} already in this session (append-only)"
                ) from e

            if text:
                conn.execute(
                    "INSERT INTO nodes_fts (text, session_id, node_id) VALUES (?, ?, ?)",
                    (text, self.session_id, node.id),
                )

            # ``last_node_id`` is the conversation HEAD — what get_branch
            # walks back from. Only top-level nodes (chat messages, with
            # no ``called_by``) may advance it. Nodes written *inside* an
            # @agentic_function run — the code Call and its nested
            # ModelCalls, all carrying ``called_by`` — are execution
            # detail, not conversation turns; letting them move the head
            # leaves it stranded on an internal node, so get_branch
            # threads the chat through code nodes and the renderer
            # mis-reports finished runs as "Execution interrupted".
            if caller_for_sql:
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (time.time(), self.session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET last_node_id = ?, updated_at = ? WHERE id = ?",
                    (node.id, time.time(), self.session_id),
                )
            conn.commit()

    def update(self, node_id: str, **fields: Any) -> None:
        """In-place update of fields on an existing node.

        Supports the "@agentic_function appends on entry, fills on exit"
        pattern: entry inserts a node with ``output=None``; exit calls
        update(node_id, output=..., metadata={...}) to fill in the result.

        Updates only the non-column fields packed inside ``data_json``;
        the FTS index is also refreshed so search reflects the new text.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, type, seq, created_at, data_json "
                "FROM nodes WHERE session_id = ? AND id = ?",
                (self.session_id, node_id),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"Node {node_id!r} not in session {self.session_id!r}"
                )
            existing = _row_to_node(row)
            for k, v in fields.items():
                if k == "metadata" and isinstance(v, dict):
                    existing.metadata = {**(existing.metadata or {}), **v}
                else:
                    setattr(existing, k, v)
            new_data = _node_to_data_json(existing)
            new_text = _node_text_for_fts(existing)
            conn.execute(
                "UPDATE nodes SET data_json = ? WHERE session_id = ? AND id = ?",
                (new_data, self.session_id, node_id),
            )
            # FTS refresh: delete old row, insert new (FTS5 has no UPDATE).
            conn.execute(
                "DELETE FROM nodes_fts WHERE session_id = ? AND node_id = ?",
                (self.session_id, node_id),
            )
            if new_text:
                conn.execute(
                    "INSERT INTO nodes_fts (text, session_id, node_id) "
                    "VALUES (?, ?, ?)",
                    (new_text, self.session_id, node_id),
                )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (time.time(), self.session_id),
            )
            conn.commit()

    def save(self, graph: Graph) -> None:
        """Persist every node in ``graph`` not yet in this session.

        Convenience for bulk-loading an in-memory graph. Each node still
        goes through the same insert path; this is just a one-shot loop.
        """
        with self._connect() as conn:
            existing = {
                r["id"] for r in conn.execute(
                    "SELECT id FROM nodes WHERE session_id = ?",
                    (self.session_id,),
                ).fetchall()
            }
        for node in graph:
            if node.id in existing:
                continue
            self.append(node)

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, *, limit: int = 50) -> list[str]:
        """Full-text search over node text. Returns node ids (most relevant first)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT node_id FROM nodes_fts
                   WHERE session_id = ? AND nodes_fts MATCH ?
                   LIMIT ?""",
                (self.session_id, query, limit),
            ).fetchall()
        return [r["node_id"] for r in rows]


# ── DB-level helpers (multi-session) ────────────────────────────────


def list_session_rows(db_path: str | Path) -> list[dict]:
    """Return all sessions in the DB, ordered newest first by updated_at."""
    p = Path(db_path).expanduser()
    if not p.exists():
        return []
    with sqlite3.connect(str(p)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def read_session_row(db_path: str | Path, session_id: str) -> Optional[dict]:
    """Read one session's row, or None if missing."""
    p = Path(db_path).expanduser()
    if not p.exists():
        return None
    with sqlite3.connect(str(p)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_session(db_path: str | Path, session_id: str) -> bool:
    """Remove a session, its nodes, snapshots, and drop blob refs.

    Returns True if a session was deleted.

    Snapshot blob 处理: 先把这个 session 所有 snapshot 引用的 hash 拿出
    来做 refcount -1, 然后删 snapshot 行, 最后 GC refcount=0 的 blob.
    """
    p = Path(db_path).expanduser()
    if not p.exists():
        return False
    with sqlite3.connect(str(p)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # FTS rows aren't tied via FK — wipe them explicitly.
        conn.execute(
            "DELETE FROM nodes_fts WHERE session_id = ?", (session_id,),
        )
        # Snapshot blob refcount: 把这个 session 的所有 snapshot.items_json
        # 解析出来, 对每个 rendered_hash refcount -1. 然后删 snapshot
        # 行, 最后 GC.
        try:
            import json
            rows = conn.execute(
                "SELECT items_json FROM context_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for (items_json,) in rows:
                try:
                    items = json.loads(items_json)
                except (TypeError, ValueError):
                    continue
                for it in items:
                    h = it.get("rendered_hash") or ""
                    if h:
                        conn.execute(
                            "UPDATE context_blobs SET refcount = refcount - 1 "
                            "WHERE hash = ?",
                            (h,),
                        )
            conn.execute(
                "DELETE FROM context_snapshots WHERE session_id = ?",
                (session_id,),
            )
            # GC blob refcount <= 0
            conn.execute("DELETE FROM context_blobs WHERE refcount <= 0")
        except sqlite3.OperationalError:
            # snapshot/blob 表可能还没建 (老 DB), 跳过
            pass
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def search_across_sessions(db_path: str | Path, query: str, *, limit: int = 50) -> list[tuple[str, str]]:
    """Full-text search the whole DB. Returns ``[(session_id, node_id), ...]``."""
    p = Path(db_path).expanduser()
    if not p.exists():
        return []
    with sqlite3.connect(str(p)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT session_id, node_id FROM nodes_fts
               WHERE nodes_fts MATCH ? LIMIT ?""",
            (query, limit),
        ).fetchall()
    return [(r["session_id"], r["node_id"]) for r in rows]


# The GraphStore the dispatcher installed for this turn. Deep code
# (Runtime.exec, ask_user) reads it to write to the SQLite DAG without
# threading a parameter through every layer. Default None = standalone
# (no persistence). Use Python's ContextVar API directly:
#   ``token = _store.set(store)`` to install, ``_store.reset(token)``
#   to tear down, ``_store.get()`` to read.
_store: ContextVar[Optional["GraphStore"]] = ContextVar(
    "_store", default=None,
)


__all__ = [
    "GraphStore",
    "init_db",
    "list_session_rows",
    "read_session_row",
    "delete_session",
    "search_across_sessions",
]
