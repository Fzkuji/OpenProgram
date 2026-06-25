"""Message-dict ⇄ Call-node translation helpers.

Lifted out of the retired ``openprogram/context/session_db.py``. The
legacy ``DagSessionDB`` adapter (SQLite-backed) is gone, but these
pure-function translators are still needed by:

  * ``openprogram/store/session_store.py`` — the new git-backed
    SessionStore exposes message-dict shapes on its boundary for
    backward compat with dispatcher / channels / webui call sites.
  * ``openprogram/agent/dispatcher.py`` — the placeholder-update
    path converts a tiny message-shape patch back into Call fields.

No SQLite, no I/O — just shape conversion. Keep stateless.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from openprogram.context.nodes import (
    Call,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
)


_USER_NATIVE = {"id", "role", "content", "timestamp"}
_ASSISTANT_NATIVE = {"id", "role", "content", "timestamp", "token_model"}
_TOOL_NATIVE = {"id", "role", "content", "timestamp", "function", "extra"}


def _decode_extra(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _msg_to_node(msg: dict) -> Call:
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
        meta.pop("parent_id", None)
        # Discriminator: a model-emitted tool_use code node carries a
        # tool_call_id (so the renderer can round-trip it as a real
        # ToolCall/ToolResult pair). Direct @agentic_function code nodes
        # have none and render as a user/assistant text pair. The id IS
        # the tool_call_id (base_id == "{assistant}_t_{tid}" or the tid),
        # surfaced explicitly here so render.py needn't parse the id.
        meta["tool_call_id"] = tool_use.get("tool_call_id") or base_id
        return Call(
            id=base_id,
            created_at=created_at,
            role=ROLE_CODE,
            name=tool_use.get("name") or msg.get("function") or "",
            input=tool_use.get("arguments") or {},
            output=msg.get("content"),
            called_by=called_by,
            metadata=meta,
        )
    meta = {k: v for k, v in msg.items() if k not in _ASSISTANT_NATIVE}
    if "extra" in meta:
        decoded = _decode_extra(meta.pop("extra"))
        for k, v in decoded.items():
            meta.setdefault(k, v)
    if role == "system":
        meta["role"] = "system"
    # Attach-pointer rows ride the assistant role but carry a
    # ``called_by`` pointing at the user turn that triggered the
    # spawn — they hang off that turn as a side-child, not as the
    # next conv step. Surfacing called_by onto the Call so
    # list_branches' "tips have no caller" filter skips them.
    called_by = meta.pop("called_by", None) or ""
    return Call(
        id=base_id,
        created_at=created_at,
        role=ROLE_LLM,
        name=msg.get("token_model") or "",
        output=msg.get("content") or "",
        called_by=called_by,
        metadata=meta,
    )


def _node_to_msg(node: Call, session_id: str) -> dict:
    meta = dict(node.metadata or {})

    # streaming-resume schema (docs/design/runtime/streaming-resume.md)
    # Every msg dict carries a ``status`` so the chat can tell at a
    # glance whether the producer is still running. Legacy nodes
    # without an explicit status default to ``done`` (they were
    # written by the pre-streaming-resume code path which only
    # persisted finished messages).
    meta.setdefault("status", "done")

    if node.is_user():
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": "user",
            "content": node.output or "",
            "parent_id": node.called_by,
            "caller": node.called_by or "",
            "timestamp": node.created_at,
        }
        base.update(meta)
        return base

    if node.is_code():
        called_by = meta.pop("called_by", None) or ""
        # tool_call_id lives inside the tool_use blob (symmetric with
        # _msg_to_node, which reads it from there). pop from meta so it
        # doesn't leak as a stray top-level field via base.update(meta).
        _tcid = meta.pop("tool_call_id", None)
        _tu = {
            "name": node.name,
            "arguments": node.input or {},
            "called_by": called_by,
        }
        if _tcid:
            _tu["tool_call_id"] = _tcid
        extra_blob = {"tool_use": _tu}
        if isinstance(meta.get("extra"), dict):
            extra_blob.update(meta.pop("extra"))
        result = node.output
        # ``ensure_ascii=False`` so Chinese / non-ASCII characters in
        # tool output render naturally in chat instead of as ``\uXXXX``
        # escape sequences. Same for ``extra`` (which carries the
        # call's input args, often containing user-typed text).
        content = (
            json.dumps(result, ensure_ascii=False, default=str)
            if not isinstance(result, str) else result
        )
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": "tool",
            "content": content,
            "parent_id": node.called_by,
            "caller": node.called_by or called_by or "",
            "timestamp": node.created_at,
            "function": node.name,
            "extra": json.dumps(extra_blob, ensure_ascii=False, default=str),
        }
        base.update(meta)
        return base

    if node.is_llm():
        legacy_role = meta.pop("role", None) or "assistant"
        base = {
            "id": node.id,
            "session_id": session_id,
            "role": legacy_role,
            "content": node.output or "",
            # parent_id falls back to called_by here, but meta.parent_id
            # (set by _msg_to_node from the original msg) is the real
            # answer and overrides via the base.update(meta) below.
            "parent_id": node.called_by,
            "caller": node.called_by or "",
            "timestamp": node.created_at,
            "token_model": node.name,
        }
        base.update(meta)
        # Restore called_by AFTER meta merge so attach-pointer rows
        # (which set called_by but no parent_id) keep their pointer
        # tag for the ws_actions/session.py splicer.
        if node.called_by:
            base["called_by"] = node.called_by
        return base

    return {
        "id": node.id,
        "session_id": session_id,
        "role": node.role or "unknown",
        "content": str(node.output or ""),
        "parent_id": node.called_by,
        "timestamp": node.created_at,
    }


def _row_to_session(row: dict) -> dict[str, Any]:
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
    for k, v in extra.items():
        out.setdefault(k, v)
    return out
