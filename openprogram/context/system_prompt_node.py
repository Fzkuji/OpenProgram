"""Record the assembled system prompt as a DAG node (dag/overview.md §7).

The prompt that shipped is data, not an implication of today's code. Whenever
the assembled text's hash changes — session start, toolset change, plan-mode
toggle — we append one ``role=code`` node named ``context/system_prompt`` with
the full text as its output. Replaying any historical call can then reproduce
the prompt that was actually sent, instead of re-assembling it from a codebase
that has moved on.

Shape (§3): ``caller="ROOT"``, ``predecessor=None``. The write invariant only
constrains conversational nodes (role user/llm), so a code node needs no
predecessor; and because ``caller`` is set, the store does not advance head —
recording the prompt never moves the branch tip.

``context/*`` names are reserved machinery and hidden from the chat transcript,
the same way ``context/summary`` nodes are.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Optional

NODE_NAME = "context/system_prompt"

#: Prefix marking a node as context machinery rather than conversation.
CONTEXT_NAME_PREFIX = "context/"


def prompt_hash(text: str) -> str:
    """Stable short hash of a system prompt."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def is_context_node(node_or_msg: Any) -> bool:
    """True for machinery nodes whose name is under ``context/``.

    Accepts a ``Call``, a message dict, or a bare name.
    """
    if isinstance(node_or_msg, str):
        name = node_or_msg
    elif isinstance(node_or_msg, dict):
        name = node_or_msg.get("name") or (
            (node_or_msg.get("metadata") or {}).get("name") or "")
    else:
        name = getattr(node_or_msg, "name", "") or ""
    return str(name).startswith(CONTEXT_NAME_PREFIX)


def latest_recorded_prompt(store: Any, session_id: str) -> Optional[str]:
    """The most recent ``context/system_prompt`` text in this session, or None.

    Reads the RAW node list (``get_messages`` hides ``context/*`` machinery
    from conversation consumers); the newest by seq wins.
    """
    try:
        nodes = store.get_nodes(session_id) or []
    except Exception:
        return None
    for msg in reversed(nodes):
        if _name_of(msg) == NODE_NAME:
            return _output_of(msg)
    return None


def record_system_prompt(store: Any, session_id: str, text: str) -> Optional[str]:
    """Append a ``context/system_prompt`` node when ``text``'s hash changed.

    Returns the new node id, or None when the hash was unchanged (or on any
    failure — recording the prompt must never break a turn).
    """
    if not session_id or not text:
        return None
    try:
        previous = latest_recorded_prompt(store, session_id)
        if previous is not None and prompt_hash(previous) == prompt_hash(text):
            return None

        from openprogram.context.nodes import Call, ROLE_CODE
        from openprogram.store import SessionNodeWriter

        node = Call(
            id="ctxsp_" + uuid.uuid4().hex[:10],
            created_at=time.time(),
            role=ROLE_CODE,
            name=NODE_NAME,
            output=text,
            caller="ROOT",
            predecessor=None,
            metadata={"display": "runtime",
                      "prompt_hash": prompt_hash(text)},
        )
        SessionNodeWriter(store, session_id).append(node)
        return node.id
    except Exception:
        return None


def _name_of(msg: Any) -> str:
    if isinstance(msg, dict):
        # ``_node_to_msg`` surfaces a code node's name as ``function``.
        return str(msg.get("function") or msg.get("name")
                   or (msg.get("metadata") or {}).get("name") or "")
    return str(getattr(msg, "name", "") or "")


def _output_of(msg: Any) -> str:
    if isinstance(msg, dict):
        for key in ("output", "content"):
            val = msg.get(key)
            if isinstance(val, str):
                return val
        return ""
    return str(getattr(msg, "output", "") or "")


__all__ = [
    "NODE_NAME",
    "CONTEXT_NAME_PREFIX",
    "prompt_hash",
    "is_context_node",
    "latest_recorded_prompt",
    "record_system_prompt",
]
