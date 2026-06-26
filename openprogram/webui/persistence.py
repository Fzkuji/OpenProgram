"""
Per-session persistence — a thin facade over SessionStore.

The single source of truth is SessionStore (``openprogram/store``): one
git repo per session under ``~/.openprogram/sessions/<session_id>/``,
addressed by ``session_id`` alone. Everything here just forwards to
``default_db()``.

History note: this module used to model "sessions belong to agents"
and kept a parallel on-disk tree at
``~/.openprogram/agents/<agent_id>/sessions/<session_id>/`` (the SQLite
era). That tree is dead — SessionStore never wrote to it — yet the old
code still created/removed those ghost dirs and scanned them to
"resolve" an agent_id. That scanning is exactly what left orphan
sessions un-deletable. It's all gone now: ``agent_id`` is just a
metadata tag stored in the session's meta, never a locator.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Meta + messages
# ---------------------------------------------------------------------------

def save_meta(agent_id: str, session_id: str, meta: dict) -> None:
    """Persist conversation metadata into SessionStore.

    ``agent_id`` is stored as a metadata tag (not a locator). The
    session is keyed by ``session_id`` alone.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    meta_fields = dict(meta)
    meta_fields.pop("id", None)
    meta_fields.pop("agent_id", None)
    # `session_id` in meta is the LLM runtime's session identifier, not
    # the SessionStore primary key. Rename it before forwarding so the
    # **kwargs expansion doesn't collide with update_session's first
    # positional parameter (also called `session_id`).
    if "session_id" in meta_fields:
        meta_fields["llm_session_id"] = meta_fields.pop("session_id")
    if db.get_session(session_id) is None:
        db.create_session(session_id, agent_id, **meta_fields)
    else:
        db.update_session(session_id, agent_id=agent_id, **meta_fields)


def save_messages(agent_id: str, session_id: str, messages: list) -> None:
    """Sync the message log to SessionStore. Skips messages whose ids are
    already persisted, so callers can keep passing the full in-memory
    list without rewriting the whole transcript every turn."""
    from openprogram.agent.session_db import default_db
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
    """Return ``[(agent_id, session_id), ...]`` from SessionStore.

    With ``agent_id`` empty (default) every session is listed; pass a
    specific agent_id to filter by that metadata tag.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    rows = db.list_sessions(agent_id=agent_id or None, limit=10_000)
    return [(r.get("agent_id") or "", r["id"]) for r in rows]


def load_session(agent_id: str, session_id: str) -> Optional[dict]:
    """Return the conversation state dict (meta + messages) by session_id.

    ``agent_id`` is accepted for call-site compatibility but is NOT used
    as a filter: the session is the source of truth, addressed by
    ``session_id`` alone. (The old ``agent_id`` mismatch → None check
    silently hid sessions whose meta had an empty/legacy agent_id.)

    Messages are *aggregated* before returning: each ``role="tool"``
    message is attached to its parent assistant under a ``tool_calls``
    array. ``legacy-conv-map.ts`` renders that shape directly.
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    sess = db.get_session(session_id)
    if sess is None:
        return None

    raw_messages = db.get_messages(session_id)
    messages = aggregate_tool_messages(raw_messages)
    result = dict(sess)
    result["id"] = session_id
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

    # Extract blocks (thinking / text / tool) from the extra JSON field
    # so the frontend mapper sees them directly on the message object.
    import json as _json2
    for m in out:
        if m.get("role") != "assistant":
            continue
        extra = m.get("extra")
        if not extra:
            continue
        if isinstance(extra, str):
            try:
                extra = _json2.loads(extra)
            except Exception:
                continue
        if isinstance(extra, dict) and extra.get("blocks"):
            m["blocks"] = extra["blocks"]

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
    """Destroy a session by id. ``agent_id`` is ignored (kept only for
    call-site compatibility); SessionStore deletes by ``session_id``."""
    from openprogram.agent.session_db import default_db
    default_db().delete_session(session_id)


def resolve_agent_for_conv(session_id: str) -> Optional[str]:
    """The agent_id tag stored in this session's meta, or None.

    Used by ``cli attach`` / the webui's delete fallback to learn which
    agent a session belongs to. Reads the metadata tag from SessionStore
    (the old version scanned ghost per-agent dirs on disk and could not
    see store-native sessions at all)."""
    from openprogram.agent.session_db import default_db
    sess = default_db().get_session(session_id)
    return (sess.get("agent_id") or None) if sess else None
