"""
Per-session persistence.

Sessions belong to agents. Persistence is now SQLite-only — the
per-conversation meta + message list lives in SessionDB; the DAG
itself (function-call nodes / model-call nodes / user nodes) is
written by the regular runtime path into the same SessionDB.

Every function here takes ``agent_id`` as the first argument so the
caller is always explicit about which agent's store it's touching.
``resolve_agent_for_conv`` scans every agent's record to find the
owner of an existing session key when the caller didn't stash it.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Optional


def _sessions_root(agent_id: str) -> Path:
    from openprogram.agents.manager import sessions_dir
    return sessions_dir(agent_id)


def sessions_root(agent_id: str) -> Path:
    """Public alias."""
    return _sessions_root(agent_id)


def conv_dir(agent_id: str, session_id: str) -> Path:
    return _sessions_root(agent_id) / session_id


def resolve_agent_for_conv(session_id: str) -> Optional[str]:
    """Which agent's sessions dir contains ``session_id``? None if
    nobody. Small O(agent_count) scan — fine for UI lookups.
    """
    try:
        from openprogram.agents.manager import list_all
        for spec in list_all():
            if (_sessions_root(spec.id) / session_id).is_dir():
                return spec.id
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Locking — per-conversation so unrelated writes don't serialize each other.
# ---------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_key(agent_id: str, session_id: str) -> str:
    return f"{agent_id}/{session_id}"


def _lock_for(agent_id: str, session_id: str) -> threading.Lock:
    key = _lock_key(agent_id, session_id)
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


def _ensure_conv_dir(agent_id: str, session_id: str) -> Path:
    d = conv_dir(agent_id, session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Meta + messages (low-frequency, whole-file overwrite)
# ---------------------------------------------------------------------------

def save_meta(agent_id: str, session_id: str, meta: dict) -> None:
    """Persist conversation metadata into SessionDB."""
    from openprogram.agent.session_db import default_db
    _ensure_conv_dir(agent_id, session_id)
    db = default_db()
    meta_fields = dict(meta)
    meta_fields.pop("id", None)
    meta_fields.pop("agent_id", None)
    # `session_id` in meta is the LLM runtime's session identifier, not
    # the SessionDB primary key. Rename it before forwarding so the
    # **kwargs expansion doesn't collide with update_session's first
    # positional parameter (also called `session_id`).
    if "session_id" in meta_fields:
        meta_fields["llm_session_id"] = meta_fields.pop("session_id")
    if db.get_session(session_id) is None:
        db.create_session(session_id, agent_id, **meta_fields)
    else:
        db.update_session(session_id, agent_id=agent_id, **meta_fields)


def save_messages(agent_id: str, session_id: str, messages: list) -> None:
    """Sync the message log to SessionDB. Skips messages whose ids are
    already persisted, so callers can keep passing the full in-memory
    list without rewriting the whole transcript every turn."""
    from openprogram.agent.session_db import default_db
    _ensure_conv_dir(agent_id, session_id)
    db = default_db()
    if db.get_session(session_id) is None:
        db.create_session(session_id, agent_id)
    existing_ids = {m["id"] for m in db.get_messages(session_id)}
    new_msgs = [m for m in messages if m.get("id") and m["id"] not in existing_ids]
    if new_msgs:
        db.append_messages(session_id, new_msgs)


def save_conversation(agent_id: str, session_id: str,
                      meta: dict, messages: list) -> None:
    """Save both meta and messages in one call."""
    save_meta(agent_id, session_id, meta)
    save_messages(agent_id, session_id, messages)


# ---------------------------------------------------------------------------
# Whole-conversation I/O
# ---------------------------------------------------------------------------

def list_sessions(agent_id: str = "") -> list[tuple[str, str]]:
    """Return ``[(agent_id, session_id), ...]`` across SessionDB.

    With ``agent_id`` empty (default) we list every agent; otherwise
    just that agent.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    rows = db.list_sessions(agent_id=agent_id or None, limit=10_000)
    return [(r["agent_id"], r["id"]) for r in rows]


def load_session(agent_id: str, session_id: str) -> Optional[dict]:
    """Return the conversation state dict (meta + messages).

    Reads SessionDB for meta + messages. Function-tree visualisation
    now reads the DAG directly off the same database via the regular
    GraphStore APIs — no separate JSONL store.

    Messages are *aggregated* before returning: each ``role="tool"``
    message is attached to its parent assistant under a ``tool_calls``
    array and removed from the top-level list. The webui frontend's
    ``legacy-conv-map.ts`` already knows how to render that shape —
    keeping the aggregation here means clients (and the WS bootstrap)
    only have to render, not reconstruct, the chat history.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    sess = db.get_session(session_id)
    if sess is None:
        return None
    if sess.get("agent_id") != agent_id:
        return None

    raw_messages = db.get_messages(session_id)
    messages = aggregate_tool_messages(raw_messages)
    result = dict(sess)
    result["id"] = session_id
    result["agent_id"] = agent_id
    result["messages"] = messages
    return result


def aggregate_tool_messages(messages: list[dict]) -> list[dict]:
    """Fold ``role="tool"`` rows into their parent assistant message.

    The SessionDB stores each tool call as a standalone ``role="tool"``
    row whose ``parent_id`` (or ``extra.tool_use.called_by``) points at
    the assistant message that issued the call. The chat UI wants
    assistant messages to carry their tool calls inline so refresh
    sees the same shape as the live WS stream.

    The output preserves message order; tool entries are not
    dropped — they only disappear from the top-level list if they
    successfully fold into a parent. Orphans (no matching parent)
    stay where they are so the UI can still render them.
    """
    if not messages:
        return messages

    # Index assistant ids → message dict for O(1) attach.
    parents: dict[str, dict] = {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("id"):
            parents[m["id"]] = m

    out: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            parent_id = m.get("parent_id")
            # Older nodes wrote the parent under extra.tool_use.called_by
            # instead of the top-level parent_id field; honour both.
            if not parent_id:
                extra = m.get("extra")
                if isinstance(extra, str):
                    try:
                        import json as _json
                        extra = _json.loads(extra)
                    except Exception:  # noqa: BLE001
                        extra = None
                if isinstance(extra, dict):
                    parent_id = (extra.get("tool_use") or {}).get("called_by")
            parent = parents.get(parent_id) if parent_id else None
            if parent is not None:
                tool_call = {
                    "tool_call_id": m.get("id", ""),
                    "tool": m.get("function") or "",
                    "input": _stringify_input(m),
                    "result": m.get("content", ""),
                    "is_error": bool(m.get("is_error")),
                }
                parent.setdefault("tool_calls", []).append(tool_call)
                continue  # don't emit a top-level entry
        out.append(m)
    return out


def _stringify_input(tool_msg: dict) -> str:
    """Pull the tool-call arguments out of the persisted shape.

    ``extra`` may be a JSON string or already a dict; the legacy path
    stored ``{"tool_use": {"arguments": {...}}}``. Returns a JSON
    string so the frontend's ``compactArgs`` parser can read it the
    same way as live ``tool_call_started`` events.
    """
    extra = tool_msg.get("extra")
    if isinstance(extra, str):
        try:
            import json as _json
            extra = _json.loads(extra)
        except Exception:  # noqa: BLE001
            extra = None
    if isinstance(extra, dict):
        tu = extra.get("tool_use")
        if isinstance(tu, dict):
            args = tu.get("arguments")
            if args is None:
                return ""
            if isinstance(args, str):
                return args
            try:
                import json as _json
                return _json.dumps(args, ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001
                return str(args)
    return ""


def delete_session(agent_id: str, session_id: str) -> None:
    from openprogram.agent.session_db import default_db
    default_db().delete_session(session_id)
    d = conv_dir(agent_id, session_id)
    if d.is_dir():
        shutil.rmtree(d)
    with _locks_guard:
        _locks.pop(_lock_key(agent_id, session_id), None)


# The legacy-file migration shim is retired — fresh installs never had
# a visualizer_sessions.json, and users who used to have one were
# already migrated before this refactor.

def migrate_legacy_file() -> int:
    return 0
