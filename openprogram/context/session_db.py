"""DagSessionDB — SessionDB-compatible API backed by the flat-DAG store.

Goal: let ``dispatcher.py`` / channels / webui keep their existing
``SessionDB``-shaped call sites while persistence flows through
``DagSessionManager`` underneath. Each row in the legacy ``messages``
table maps to a Graph node:

    role="user"      → UserMessage     (content from msg["content"])
    role="assistant" → ModelCall       (output from msg["content"])
    role="tool"      → FunctionCall    (result from msg["content"];
                                        function_name + arguments from
                                        msg["extra"]["tool_use"] if present)

This adapter implements only the methods ``dispatcher`` and its
direct collaborators call (14 methods). Advanced features that don't
have a natural DAG mapping yet (named branches, per-branch token
stats) are stubbed — they return empty / zero / no-op.

Migration strategy:
  - Set ``OPENPROGRAM_DAG_SESSION_DB=1`` in the environment to make
    ``default_db()`` return DagSessionDB instead of SessionDB.
  - Old SQLite data is NOT migrated. Existing chat history is left
    behind. Per user direction.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
)
from openprogram.context.storage import (
    GraphStore,
    init_db,
    list_session_rows,
    read_session_row,
    delete_session as _delete_session_row,
    search_across_sessions as _search_across_sessions,
)


# ── Node ↔ message-dict translation ────────────────────────────────


# Fields handled natively by the Call dataclass; everything else falls
# through into Call.metadata for roundtrip. ``parent_id`` is preserved
# in metadata explicitly — old chat dispatchers rely on it to walk the
# message tree, but the flat-DAG Call type doesn't model it natively.
_CORE_FIELDS = {"id", "role", "content", "timestamp"}
_USER_NATIVE = {"id", "role", "content", "timestamp"}
_ASSISTANT_NATIVE = {"id", "role", "content", "timestamp", "token_model"}
_TOOL_NATIVE = {"id", "role", "content", "timestamp", "function", "extra"}


def _msg_to_node(msg: dict) -> Call:
    """Build a Call node from a legacy message dict.

    Maps legacy chat roles to Call roles:
        user        → role=user   output = content
        assistant   → role=llm    output = content, name = token_model
        tool        → role=code   input = arguments, output = result, name = function
        system      → role=llm    metadata.role = "system" preserves origin

    Non-core fields land in ``node.metadata``; ``extra`` JSON blobs are
    decoded and merged (so attachments/manifest live at the top level
    on read).
    """
    role = msg.get("role", "user")
    base_id = msg.get("id") or uuid.uuid4().hex[:12]
    predecessor = msg.get("parent_id")
    created_at = msg.get("timestamp") or time.time()

    if role == "user":
        meta = {k: v for k, v in msg.items() if k not in _USER_NATIVE}
        if "extra" in meta:
            decoded = _decode_extra(meta.pop("extra"))
            for k, v in decoded.items():
                meta.setdefault(k, v)
        return Call(
            id=base_id,
            created_at=created_at,
            role=ROLE_USER,
            output=msg.get("content") or "",
            metadata=meta,
        )
    if role == "tool":
        extra = _decode_extra(msg.get("extra"))
        tool_use = extra.get("tool_use") or {}
        meta = {k: v for k, v in msg.items() if k not in _TOOL_NATIVE}
        leftover_extra = {k: v for k, v in extra.items() if k != "tool_use"}
        if leftover_extra:
            meta["extra"] = leftover_extra
        called_by = tool_use.get("called_by") or predecessor or ""
        if called_by:
            meta["called_by"] = called_by
        return Call(
            id=base_id,
            created_at=created_at,
            role=ROLE_CODE,
            name=tool_use.get("name") or msg.get("function") or "",
            input=tool_use.get("arguments") or {},
            output=msg.get("content"),
            metadata=meta,
        )
    # assistant / system / anything else → llm Call
    meta = {k: v for k, v in msg.items() if k not in _ASSISTANT_NATIVE}
    if "extra" in meta:
        decoded = _decode_extra(meta.pop("extra"))
        for k, v in decoded.items():
            meta.setdefault(k, v)
    if role == "system":
        meta["role"] = "system"   # preserve origin for roundtrip
    return Call(
        id=base_id,
        created_at=created_at,
        role=ROLE_LLM,
        name=msg.get("token_model") or "",
        output=msg.get("content") or "",
        metadata=meta,
    )


def _node_to_msg(node: Call, session_id: str) -> dict:
    """Render a Call back into the legacy message-dict shape consumed by
    dispatcher / channels / webui.

    Legacy passthrough fields are restored from ``node.metadata``.
    """
    meta = dict(node.metadata or {})

    if node.is_user():
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": "user",
            "content": node.output or "",
            "parent_id": node.called_by,
            "timestamp": node.created_at,
        }
        base.update(meta)
        return base

    if node.is_code():
        called_by = meta.pop("called_by", None) or ""
        extra_blob = {"tool_use": {
            "name": node.name,
            "arguments": node.input or {},
            "called_by": called_by,
        }}
        if isinstance(meta.get("extra"), dict):
            extra_blob.update(meta.pop("extra"))
        result = node.output
        content = (
            json.dumps(result, default=str)
            if not isinstance(result, str) else result
        )
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": "tool",
            "content": content,
            "parent_id": node.called_by,
            "timestamp": node.created_at,
            "function": node.name,
            "extra": json.dumps(extra_blob, default=str),
        }
        base.update(meta)
        return base

    if node.is_llm():
        # metadata.role lets us round-trip role="system" assistants
        # back to "system" instead of the default "assistant".
        legacy_role = meta.pop("role", None) or "assistant"
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": legacy_role,
            "content": node.output or "",
            "parent_id": node.called_by,
            "timestamp": node.created_at,
            "token_model": node.name,
        }
        base.update(meta)
        return base

    # Unknown role — return a minimal envelope so callers don't crash.
    return {
        "id": node.id,
        "session_id": session_id,
        "role": node.role or "unknown",
        "content": str(node.output or ""),
        "parent_id": node.called_by,
        "timestamp": node.created_at,
    }


def _decode_extra(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Adapter ─────────────────────────────────────────────────────────


def _default_dag_db_path() -> Path:
    """Default location for the DAG-backed SQLite file."""
    from openprogram.paths import get_state_dir
    return Path(get_state_dir()) / "dag_sessions.sqlite"


class DagSessionDB:
    """SessionDB-shaped adapter over a single DagSessionManager DB file."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else _default_dag_db_path()
        init_db(self.db_path)
        self._ensure_aux_tables()

    def _ensure_aux_tables(self) -> None:
        """Adapter-owned tables: branch names, etc. Lives alongside
        the canonical DAG tables (sessions, nodes, nodes_fts)."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS branch_names (
                    session_id   TEXT NOT NULL,
                    head_node_id TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL,
                    PRIMARY KEY (session_id, head_node_id)
                )
            """)
            conn.commit()

    # ── Session CRUD ─────────────────────────────────────────────

    def create_session(
        self,
        session_id: str,
        agent_id: str,
        *,
        title: str = "",
        source: Optional[str] = None,
        channel: Optional[str] = None,
        peer_display: Optional[str] = None,
        peer_id: Optional[str] = None,
        **other_fields: Any,
    ) -> None:
        store = GraphStore(self.db_path, session_id)
        if store.session_exists():
            return
        extra: dict = {}
        if channel:
            extra["channel"] = channel
        if peer_display:
            extra["peer_display"] = peer_display
        if peer_id:
            extra["peer_id"] = peer_id
        # Any other kwargs (tools_enabled / thinking_effort /
        # permission_mode / peer_kind / account_id / ...) land in
        # extra_json so dispatcher's SessionRunConfig roundtrips.
        for k, v in other_fields.items():
            if v is not None:
                extra[k] = v
        store.create_session_row(
            title=title,
            agent_id=agent_id,
            source=source or "",
            extra=extra,
        )

    def update_session(self, session_id: str, **fields: Any) -> None:
        store = GraphStore(self.db_path, session_id)
        # Direct columns: skip None to avoid clobbering with NULL.
        direct: dict = {}
        for k in ("title", "model", "agent_id", "source"):
            if fields.get(k) is not None:
                direct[k] = fields[k]
        if direct:
            store.update_session_row(**direct)
        # head_id → advance last_node_id
        if fields.get("head_id") is not None:
            self.set_head(session_id, fields["head_id"])
        # Everything else (token counts, channel binding, _titled
        # flag, etc.) lands in extra_json so it roundtrips through
        # get_session() as session["extra_meta"].
        direct_cols = {"title", "model", "agent_id", "source", "head_id"}
        aux = {k: v for k, v in fields.items()
               if k not in direct_cols and v is not None}
        if aux:
            row = read_session_row(self.db_path, session_id) or {}
            existing = _decode_extra(row.get("extra_json"))
            existing.update(aux)
            store.update_session_row(extra=existing)

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        row = read_session_row(self.db_path, session_id)
        if row is None:
            return None
        return _row_to_session(row)

    def delete_session(self, session_id: str) -> None:
        _delete_session_row(self.db_path, session_id)

    def list_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        rows = list_session_rows(self.db_path)
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if source is not None:
            rows = [r for r in rows if r.get("source") == source]
        rows = rows[offset:offset + limit]
        return [_row_to_session(r) for r in rows]

    def count_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        rows = list_session_rows(self.db_path)
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if source is not None:
            rows = [r for r in rows if r.get("source") == source]
        return len(rows)

    # ── Message append / read ────────────────────────────────────

    def append_message(self, session_id: str, msg: dict[str, Any]) -> None:
        store = GraphStore(self.db_path, session_id)
        if not store.session_exists():
            store.create_session_row()
        node = _msg_to_node(msg)
        try:
            store.append(node)
        except ValueError:
            # Already persisted — idempotent
            pass

    def append_messages(self, session_id: str, msgs: list[dict[str, Any]]) -> None:
        for m in msgs:
            self.append_message(session_id, m)

    def get_messages(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        store = GraphStore(self.db_path, session_id)
        graph = store.load()
        msgs = [_node_to_msg(n, session_id) for n in graph]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs

    def get_branch(
        self,
        session_id: str,
        head_msg_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Walk back from ``head_msg_id`` (or session head) through the
        ``metadata.parent_id`` chain — that's where the legacy chat
        message-tree edge lives. Falls back to ``called_by`` for nodes
        that don't carry parent_id (e.g. nodes the bridge wrote)."""
        store = GraphStore(self.db_path, session_id)
        graph = store.load()
        if head_msg_id is None:
            # Active head lives in the sessions table (set by set_head).
            # Falling back to graph._last_id would always pick the most
            # recently appended node and ignore explicit set_head calls.
            row = read_session_row(self.db_path, session_id) or {}
            head_msg_id = row.get("last_node_id") or graph._last_id
        if head_msg_id is None or head_msg_id not in graph.nodes:
            return []
        chain: list = []
        cur: Optional[str] = head_msg_id
        seen: set[str] = set()
        while cur is not None and cur in graph.nodes and cur not in seen:
            seen.add(cur)
            node = graph.nodes[cur]
            chain.append(node)
            parent = (node.metadata or {}).get("parent_id")
            if not parent:
                parent = node.called_by or None
            cur = parent if parent else None
        chain.reverse()
        return [_node_to_msg(n, session_id) for n in chain]

    # ── Head pointer ─────────────────────────────────────────────

    def set_head(self, session_id: str, head_id: Optional[str]) -> None:
        # The store's append() already advances last_node_id; this is
        # only called when the dispatcher wants to fork (re-point head
        # to a different message). Update last_node_id directly.
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE sessions SET last_node_id = ?, updated_at = ? WHERE id = ?",
                (head_id, time.time(), session_id),
            )
            conn.commit()

    # ── Generic node existence check ─────────────────────────────

    def message_exists(self, session_id: str, msg_id: str) -> bool:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM nodes WHERE id = ? AND session_id = ?",
                (msg_id, session_id),
            ).fetchone()
            return row is not None

    # ── Named branches ───────────────────────────────────────────
    #
    # A "branch" in the legacy schema is a tip of the message tree:
    # any node with no children. With named branches a human-readable
    # label gets attached to a specific head id. ``list_branches``
    # returns every tip — named or not — so the UI can show all
    # divergent endpoints, with the saved name (or None) on each.

    def list_branches(self, session_id: str) -> list[dict[str, Any]]:
        # 1) Find every node with no successor → that's a branch tip.
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            tips = conn.execute(
                """
                SELECT n.id, n.created_at
                FROM nodes n
                WHERE n.session_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM nodes c
                    WHERE c.session_id = n.session_id
                      AND c.predecessor = n.id
                  )
                """,
                (session_id,),
            ).fetchall()
            named = {r["head_node_id"]: r for r in conn.execute(
                "SELECT head_node_id, name, created_at, updated_at "
                "FROM branch_names WHERE session_id = ?",
                (session_id,),
            ).fetchall()}
        out: list[dict[str, Any]] = []
        for tip in tips:
            label_row = named.get(tip["id"])
            out.append({
                "head_msg_id": tip["id"],
                "name": label_row["name"] if label_row else None,
                "created_at": (label_row["created_at"]
                                if label_row else tip["created_at"]),
                "updated_at": (label_row["updated_at"]
                                if label_row else tip["created_at"]),
            })
        # Most-recent first.
        out.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        return out

    def set_branch_name(self, session_id: str, head_msg_id: str,
                        name: str) -> None:
        now = time.time()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO branch_names (session_id, head_node_id,
                                          name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, head_node_id) DO UPDATE
                  SET name = excluded.name,
                      updated_at = excluded.updated_at
                """,
                (session_id, head_msg_id, name, now, now),
            )
            conn.commit()

    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "DELETE FROM branch_names "
                "WHERE session_id = ? AND head_node_id = ?",
                (session_id, head_msg_id),
            )
            conn.commit()

    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        """Delete ``head_msg_id`` plus every node downstream of it.
        Returns the number of nodes deleted."""
        # Build children map and BFS from head_msg_id.
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, predecessor FROM nodes WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            children: dict[str, list[str]] = {}
            ids_seen = set()
            for r in rows:
                ids_seen.add(r["id"])
                if r["predecessor"]:
                    children.setdefault(r["predecessor"], []).append(r["id"])
            if head_msg_id not in ids_seen:
                return 0
            to_delete: list[str] = []
            stack = [head_msg_id]
            while stack:
                cur = stack.pop()
                to_delete.append(cur)
                stack.extend(children.get(cur, []))
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})",
                to_delete,
            )
            conn.execute(
                f"DELETE FROM nodes_fts WHERE node_id IN ({placeholders})",
                to_delete,
            )
            conn.execute(
                "DELETE FROM branch_names "
                f"WHERE session_id = ? AND head_node_id IN ({placeholders})",
                [session_id, *to_delete],
            )
            conn.commit()
            return len(to_delete)

    # ── Token stats ──────────────────────────────────────────────
    #
    # Dispatcher stamps usage onto assistant messages
    # (input_tokens / output_tokens / cache_read_tokens /
    # cache_write_tokens). On the DAG these land in
    # ModelCall.metadata via the message→node mapping. To get the
    # branch total, walk the predecessor chain from head_msg_id and
    # sum.

    def get_branch_token_stats(
        self,
        session_id: str,
        head_msg_id: Optional[str] = None,
        *,
        head_id: Optional[str] = None,  # legacy kwarg name
        model: Any = None,              # str id OR a Model object with .id
    ) -> dict[str, Any]:
        head = head_id or head_msg_id
        chain = self.get_branch(session_id, head) if head \
                else self.get_messages(session_id)

        model_id = getattr(model, "id", None) or (
            model if isinstance(model, str) else None
        )

        input_total = 0
        output_total = 0
        cache_read_total = 0
        cache_write_total = 0
        messages_counted = 0
        last_input_tokens = 0
        last_model = None
        for m in chain:
            if m.get("role") != "assistant":
                continue
            if model_id is not None and m.get("token_model") != model_id:
                continue
            i = int(m.get("input_tokens") or 0)
            o = int(m.get("output_tokens") or 0)
            input_total += i
            output_total += o
            cache_read_total += int(m.get("cache_read_tokens") or 0)
            cache_write_total += int(m.get("cache_write_tokens") or 0)
            messages_counted += 1
            if i:
                last_input_tokens = i
            if m.get("token_model"):
                last_model = m["token_model"]

        # Approximate "what's currently sitting in context" as the
        # most-recent turn's input_tokens (matches legacy semantics).
        current_tokens = last_input_tokens + output_total // max(messages_counted, 1)
        # Cache hit rate: cache_read / (cache_read + non-cached input).
        denom = cache_read_total + input_total
        cache_hit_rate = (cache_read_total / denom) if denom else 0.0

        # context_window left at 0 here — the route layer resolves it
        # from the MODELS registry (provider differs per session).
        return {
            "input_tokens": input_total,
            "output_tokens": output_total,
            "cache_read_tokens": cache_read_total,
            "cache_write_tokens": cache_write_total,
            "cache_read_total": cache_read_total,
            "cache_hit_rate": cache_hit_rate,
            "messages_counted": messages_counted,
            "current_tokens": current_tokens,
            "context_window": 0,
            "pct_used": 0.0,
            "model": last_model or model_id,
        }

    # ── Misc utilities ───────────────────────────────────────────

    def latest_user_text(self, session_id: str) -> Optional[str]:
        store = GraphStore(self.db_path, session_id)
        graph = store.load()
        for n in reversed(list(graph)):
            if n.is_user():
                return n.content
        return None

    def sessions_with_binding(self, channel: str, account_id: Optional[str]) -> list[str]:
        rows = list_session_rows(self.db_path)
        out: list[str] = []
        for r in rows:
            extra = _decode_extra(r.get("extra_json"))
            if extra.get("channel") != channel:
                continue
            if account_id is not None and extra.get("account_id") != account_id:
                continue
            out.append(r["id"])
        return out

    def search_messages(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        # Build a session_id → session_row lookup for filter + enrichment.
        sess_rows = {r["id"]: r for r in list_session_rows(self.db_path)}

        def _enrich(msg: dict, sid: str) -> dict:
            row = sess_rows.get(sid) or {}
            msg["session_title"] = row.get("title") or ""
            msg["session_source"] = row.get("source") or ""
            msg["session_agent_id"] = row.get("agent_id") or ""
            return msg

        def _passes_agent_filter(sid: str) -> bool:
            if agent_id is None:
                return True
            return (sess_rows.get(sid) or {}).get("agent_id") == agent_id

        if session_id is not None:
            if not _passes_agent_filter(session_id):
                return []
            store = GraphStore(self.db_path, session_id)
            ids = store.search(query, limit=limit)
            graph = store.load()
            return [
                _enrich(_node_to_msg(graph.nodes[i], session_id), session_id)
                for i in ids if i in graph.nodes
            ]
        # Cross-session search
        out: list[dict[str, Any]] = []
        for sid, nid in _search_across_sessions(self.db_path, query, limit=limit):
            if not _passes_agent_filter(sid):
                continue
            store = GraphStore(self.db_path, sid)
            graph = store.load()
            if nid in graph.nodes:
                out.append(_enrich(_node_to_msg(graph.nodes[nid], sid), sid))
        return out

    def get_descendants(self, session_id: str, msg_id: str) -> list[dict[str, Any]]:
        """All nodes whose predecessor chain passes through ``msg_id``."""
        store = GraphStore(self.db_path, session_id)
        graph = store.load()
        if msg_id not in graph.nodes:
            return []
        # Build children map
        children: dict[str, list[str]] = {}
        for n in graph:
            if n.called_by:
                children.setdefault(n.called_by, []).append(n.id)
        out: list = []
        stack: list[str] = [msg_id]
        while stack:
            cur = stack.pop()
            for c in children.get(cur, []):
                out.append(graph.nodes[c])
                stack.append(c)
        return [_node_to_msg(n, session_id) for n in out]

    def get_deepest_leaf(self, session_id: str, msg_id: Optional[str] = None) -> Optional[str]:
        store = GraphStore(self.db_path, session_id)
        graph = store.load()
        # Find leaf with longest message-tree chain from msg_id.
        # The legacy parent_id lives in node.metadata.parent_id —
        # that's what defines the chat-tree edge dispatcher cares about.
        def _parent(n) -> Optional[str]:
            return (n.metadata or {}).get("parent_id") or None

        children: dict[str, list[str]] = {}
        for n in graph:
            p = _parent(n)
            if p:
                children.setdefault(p, []).append(n.id)
        roots = [msg_id] if msg_id else [n.id for n in graph if not _parent(n)]
        deepest_id: Optional[str] = None
        deepest_depth = -1
        for root in roots:
            if root not in graph.nodes:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                cur, depth = stack.pop()
                kids = children.get(cur, [])
                if not kids:
                    if depth > deepest_depth:
                        deepest_depth = depth
                        deepest_id = cur
                else:
                    for c in kids:
                        stack.append((c, depth + 1))
        return deepest_id

    def count_recent_nodes(self, since: float) -> int:
        """Count nodes (any type) created at-or-after ``since`` epoch."""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE created_at >= ?",
                (since,),
            ).fetchone()
            return int(row[0]) if row else 0

    def close(self) -> None:
        # No persistent connection to close.
        return None


def _row_to_session(row: dict) -> dict[str, Any]:
    """Format a DAG sessions row to look like a SessionDB session dict.

    All keys in extra_json get hoisted to top level so callers like
    ``load_session_run_config`` can read ``tools_enabled`` /
    ``thinking_effort`` / ``permission_mode`` directly off the dict.
    """
    extra = _decode_extra(row.get("extra_json"))
    out: dict[str, Any] = {
        "id": row["id"],
        "agent_id": row.get("agent_id") or "",
        "title": row.get("title") or "",
        "created_at": row.get("created_at") or 0,
        "updated_at": row.get("updated_at") or 0,
        "source": row.get("source") or None,
        "head_id": row.get("last_node_id"),
        "model": row.get("model") or None,
        "context_tree": None,
        "extra_meta": extra or None,
        "last_prompt_tokens": extra.get("last_prompt_tokens", 0),
    }
    # Hoist extra_json keys to top level (without clobbering the
    # native columns above).
    for k, v in extra.items():
        out.setdefault(k, v)
    return out


__all__ = ["DagSessionDB"]
