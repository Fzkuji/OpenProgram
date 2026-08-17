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


def latest_recorded_prompt(
    store: Any,
    session_id: str,
    head_id: str | None = None,
) -> Optional[str]:
    """The most recent ``context/system_prompt`` text in this session, or None.

    Reads the RAW node list (``get_messages`` hides ``context/*`` machinery
    from conversation consumers); the newest by seq wins.
    """
    try:
        nodes = store.get_nodes(session_id) or []
    except Exception:
        return None
    branch_anchors = _branch_anchor_ids(store, session_id, head_id)
    if branch_anchors == set():
        return None
    legacy_anchors = _legacy_snapshot_anchors(nodes)
    for msg in reversed(nodes):
        if (_name_of(msg) == NODE_NAME
                and _snapshot_on_branch(
                    msg,
                    branch_anchors,
                    legacy_anchor=legacy_anchors.get(_id_of(msg)),
                )):
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
        anchor_head_id = _active_head_id(store, session_id)
        previous = latest_recorded_prompt(store, session_id, anchor_head_id)
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
                      "prompt_hash": prompt_hash(text),
                      "anchor_head_id": anchor_head_id or ""},
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


def _id_of(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("id") or "")
    return str(getattr(msg, "id", "") or "")


def _metadata_of(node: Any) -> dict:
    if isinstance(node, dict):
        value = node.get("metadata")
    else:
        value = getattr(node, "metadata", None)
    return value if isinstance(value, dict) else {}


def _role_of(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("role") or "")
    return str(getattr(node, "role", "") or "")


def _seq_of(node: Any) -> int:
    if isinstance(node, dict):
        return int(node.get("seq", -1) or -1)
    return int(getattr(node, "seq", -1) or -1)


def _legacy_snapshot_anchors(nodes: list[Any]) -> dict[str, str]:
    """Infer the active HEAD for pre-anchor context nodes.

    Old recorders appended a context node immediately after assembling a
    request but left ``predecessor=None``. The nearest earlier conversational
    node is therefore the request HEAD that existed at that write.
    """
    anchors: dict[str, str] = {}
    latest_conversation_id = ""
    for node in sorted(nodes, key=_seq_of):
        if _role_of(node) in {"user", "llm", "assistant"}:
            latest_conversation_id = _id_of(node)
        elif (_name_of(node).startswith(CONTEXT_NAME_PREFIX)
              and not _metadata_of(node).get("anchor_head_id")):
            anchors[_id_of(node)] = latest_conversation_id
    return anchors


def _active_head_id(store: Any, session_id: str) -> str | None:
    try:
        from openprogram.store import _current_turn_id
        turn_id = str(_current_turn_id.get() or "")
        if turn_id:
            return turn_id
    except Exception:
        pass
    try:
        return str((store.get_session(session_id) or {}).get("head_id") or "") or None
    except Exception:
        return None


def _branch_anchor_ids(
    store: Any,
    session_id: str,
    head_id: str | None,
) -> set[str] | None:
    """Conversation node ids on the selected branch; empty means invalid HEAD."""
    selected = head_id or _active_head_id(store, session_id)
    if not selected:
        return None
    try:
        branch = store.get_branch(session_id, selected) or []
    except Exception:
        return set()
    ids = {_id_of(node) for node in branch if _id_of(node)}
    return ids if selected in ids else set()


def _snapshot_on_branch(
    node: Any,
    branch_anchors: set[str] | None,
    *,
    legacy_anchor: str | None = None,
) -> bool:
    if branch_anchors is None:
        return True
    anchor = str(
        _metadata_of(node).get("anchor_head_id") or legacy_anchor or ""
    )
    return bool(anchor and anchor in branch_anchors)


__all__ = [
    "NODE_NAME",
    "CONTEXT_NAME_PREFIX",
    "prompt_hash",
    "is_context_node",
    "latest_recorded_prompt",
    "record_system_prompt",
]
