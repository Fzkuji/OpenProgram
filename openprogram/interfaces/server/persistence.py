"""
Per-session persistence — a thin facade over SessionStore.

The single source of truth is SessionStore (``openprogram/state/store``): one
git repo per session under ``~/.openprogram/projects/<project>/chats/<session_id>/``,
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

from collections import Counter
from typing import Optional


# ---------------------------------------------------------------------------
# Meta + messages
# ---------------------------------------------------------------------------

def save_meta(agent_id: str, session_id: str, meta: dict) -> None:
    """Persist conversation metadata into SessionStore.

    ``agent_id`` is stored as a metadata tag (not a locator). The
    session is keyed by ``session_id`` alone.
    """
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    meta_fields = dict(meta)
    meta_fields.pop("id", None)
    meta_fields.pop("agent_id", None)
    # HEAD has exactly one writer — SessionStore.set_head
    # (context/compaction.md §5). Meta saves must never smuggle a
    # possibly-stale head value over it, whatever the caller passed.
    meta_fields.pop("head_id", None)
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




# ---------------------------------------------------------------------------
# Whole-conversation I/O
# ---------------------------------------------------------------------------

def list_sessions(agent_id: str = "") -> list[tuple[str, str]]:
    """Return ``[(agent_id, session_id), ...]`` from SessionStore.

    With ``agent_id`` empty (default) every session is listed; pass a
    specific agent_id to filter by that metadata tag.
    """
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    rows = db.list_sessions(agent_id=agent_id or None, limit=10_000,
                            include_archived=True)
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

    Only the ACTIVE branch is returned. A session's DAG can hold several
    sibling branches (retries / edits / forks); the head marks which one
    is current. Returning every node would leak abandoned branches into
    whatever consumes ``messages`` — the CLI turn history fed to the
    model, ``/copy``'s "last assistant", the restored ``_sessions``
    cache. We filter the aggregated set to the head's branch so all
    surfaces agree on "this conversation" the way the web chat does.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.context.git import active_branch_chain, head_or_tip
    db = default_db()
    sess = db.get_session(session_id)
    if sess is None:
        return None

    raw_messages = db.get_messages(session_id)
    messages = aggregate_tool_messages(raw_messages)
    head = sess.get("head_id") or head_or_tip(sess, messages)
    try:
        branch_ids = [
            m.get("id") for m in (db.get_branch(session_id, head) or [])
        ]
    except Exception:
        branch_ids = []
    messages = active_branch_chain(messages, branch_ids, head)
    result = dict(sess)
    result["id"] = session_id
    result["messages"] = messages
    return result


def aggregate_tool_messages(messages: list[dict]) -> list[dict]:
    """Fold ``role="tool"`` rows into their parent assistant message.

    The SessionDB stores each tool call as a standalone ``role="tool"``
    row whose ``caller`` points at the assistant message that issued
    the call — "who invoked me" is the sub-call edge, never the
    conversation chain (dag/overview.md §3). The chat UI wants
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
    late: dict[str, list[tuple[str | None, dict]]] = {}
    folded: dict[str, list] = {}
    unreplaced: dict[str, dict] = {}
    for m in messages:
        if m.get("role") == "tool":
            caller_id = m.get("caller")
            if not caller_id:
                extra = m.get("extra")
                if isinstance(extra, str):
                    try:
                        import json as _json
                        extra = _json.loads(extra)
                    except Exception:  # noqa: BLE001
                        extra = None
                if isinstance(extra, dict):
                    # _msg_adapter writes the blob key as "caller".
                    caller_id = (extra.get("tool_use") or {}).get("caller")
            parent = parents.get(caller_id) if caller_id else None
            if parent is not None:
                tool_call = {
                    "tool_call_id": m.get("id", ""),
                    "tool": m.get("function") or "",
                    "input": _stringify_input(m),
                    "result": m.get("content", ""),
                    "is_error": bool(m.get("is_error")),
                }
                provider_id = _provider_call_id(m)
                calls = folded.get(parent["id"])
                if calls is None:
                    # A reply's stored list can be the node cache's own
                    # object, so folding works on a copy.
                    calls = folded[parent["id"]] = list(parent.get("tool_calls") or [])
                    parent["tool_calls"] = calls
                    unreplaced[parent["id"]] = _call_positions(calls)
                positions = unreplaced[parent["id"]].get(provider_id) if provider_id else None
                if positions:
                    calls[positions.pop()] = tool_call
                else:
                    calls.append(tool_call)
                late.setdefault(parent["id"], []).append((provider_id, tool_call))
                continue  # don't emit a top-level entry
        out.append(m)

    # Extract blocks (thinking / text / tool) from the extra JSON field
    # so the frontend mapper sees them directly on the message object.
    # A reply written through the append fallback has no ``extra``: its
    # blocks are already top-level.
    import json as _json2
    for m in out:
        if m.get("role") != "assistant":
            continue
        extra = m.get("extra")
        if isinstance(extra, str):
            try:
                extra = _json2.loads(extra)
            except Exception:
                extra = None
        if not isinstance(extra, dict):
            extra = {}
        if extra.get("stream_seq"):
            # The last stream frame a still-running reply's content
            # reflects; the client applies only later frames on top.
            m["stream_seq"] = extra["stream_seq"]
        blocks = extra.get("blocks") or m.get("blocks")
        if blocks:
            # Blocks are frozen when the turn ends. A call attached later,
            # such as a delivered self-update result, exists only as a row.
            tool_blocks = [b for b in blocks if b.get("type") == "tool"]
            recorded = {b.get("tool_call_id") for b in tool_blocks}
            # An old block and its row can both lack an id; the name pairs them.
            unnamed = Counter(b.get("tool") for b in tool_blocks if not b.get("tool_call_id"))
            attached = []
            for call_id, call in late.get(m["id"], ()):
                if call_id is None and unnamed[call["tool"]] > 0:
                    unnamed[call["tool"]] -= 1
                elif call_id is None or call_id not in recorded:
                    attached.append({"type": "tool", **call})
            m["blocks"] = [*blocks, *attached]

    return out


def _call_positions(calls: list) -> dict[str, list[int]]:
    """Where each stored call id sits, earliest position last.

    A row replaces its stored call once and then carries the row's own id, so
    a match pops its position and a repeated id reaches the next stored call.
    """
    positions: dict[str, list[int]] = {}
    for index in range(len(calls) - 1, -1, -1):
        call = calls[index]
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        if isinstance(call_id, str):
            positions.setdefault(call_id, []).append(index)
    return positions


def _provider_call_id(tool_msg: dict) -> str | None:
    extra = tool_msg.get("extra")
    if isinstance(extra, str):
        try:
            import json as _json
            extra = _json.loads(extra)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(extra, dict):
        return (extra.get("tool_use") or {}).get("tool_call_id")
    return None


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
    from openprogram.runtime.context.session_db import default_db
    default_db().delete_session(session_id)


def resolve_agent_for_conv(session_id: str) -> Optional[str]:
    """The agent_id tag stored in this session's meta, or None.

    Used by ``cli attach`` / the webui's delete fallback to learn which
    agent a session belongs to. Reads the metadata tag from SessionStore
    (the old version scanned ghost per-agent dirs on disk and could not
    see store-native sessions at all)."""
    from openprogram.runtime.context.session_db import default_db
    sess = default_db().get_session(session_id)
    return (sess.get("agent_id") or None) if sess else None
