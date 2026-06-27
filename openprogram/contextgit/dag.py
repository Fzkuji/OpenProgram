"""Pure DAG helpers over a list of message dicts.

Each message links to its parent via ``called_by``.

Contract:

* ``siblings(msgs, msg_id)`` — messages sharing a parent with
  ``msg_id``, including ``msg_id`` itself.
* ``children(msgs, msg_id)`` — messages whose parent is ``msg_id``.
* ``linear_history(msgs, head_id)`` — walk ``called_by`` from
  ``head_id`` back to the root, return list in root-first order.
* ``is_ancestor(msgs, anc_id, desc_id)`` — whether ``anc_id`` is
  reachable from ``desc_id`` via ``called_by``.
* ``normalize_parent_pointers(msgs)`` — migration helper for legacy
  conversations without ``called_by``.
* ``head_or_tip(conv, msgs)`` — return the conversation's ``head_id``
  if set; otherwise the last message's id.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol


class MessageLike(Protocol):
    """Duck type for the dicts we operate on. Nothing else matters."""

    def __getitem__(self, key: str) -> Any: ...
    def get(self, key: str, default: Any = ...) -> Any: ...


def _parent_of(m: MessageLike) -> Optional[str]:
    """Return the parent pointer of a message node.

    Uses ``called_by`` exclusively. Returns None for root nodes
    (called_by=ROOT or empty/missing).
    """
    cb = m.get("called_by")
    if cb and cb != "ROOT":
        return cb
    return None


def _index_by_id(msgs: Iterable[MessageLike]) -> dict[str, MessageLike]:
    return {m["id"]: m for m in msgs if m.get("id")}


def _sorted_by_created_at(items: Iterable[MessageLike]) -> list[MessageLike]:
    """Stable sort by ``created_at``; missing timestamps sort last in
    insertion order. We preserve insertion order as the tiebreaker so
    legacy messages without timestamps still render deterministically."""
    listed = list(items)
    return sorted(listed, key=lambda m: (m.get("created_at") or 0, listed.index(m)))


def siblings(msgs: list[MessageLike], msg_id: str) -> list[MessageLike]:
    """Return messages sharing a parent with ``msg_id`` (includes itself).

    Root messages (parent is None) are siblings of all other root
    messages. Unknown ``msg_id`` returns ``[]``.
    """
    by_id = _index_by_id(msgs)
    target = by_id.get(msg_id)
    if target is None:
        return []
    target_parent = _parent_of(target)
    return _sorted_by_created_at(
        m for m in msgs if _parent_of(m) == target_parent
    )


def sibling_index(msgs: list[MessageLike], msg_id: str) -> tuple[int, int]:
    """Return ``(index, total)`` for ``msg_id`` within its sibling set.

    Both 1-indexed for UI convenience. Returns ``(0, 0)`` if
    ``msg_id`` is unknown."""
    sibs = siblings(msgs, msg_id)
    ids = [s["id"] for s in sibs]
    if msg_id not in ids:
        return (0, 0)
    return (ids.index(msg_id) + 1, len(ids))


def children(msgs: list[MessageLike], msg_id: str) -> list[MessageLike]:
    """Messages whose parent is ``msg_id``, ordered by creation."""
    return _sorted_by_created_at(
        m for m in msgs if _parent_of(m) == msg_id
    )


def linear_history(msgs: list[MessageLike], head_id: str) -> list[MessageLike]:
    """Walk from ``head_id`` back to the root along ``called_by``.

    Returns messages in root-first order.

    Tolerates cycles (shouldn't happen but we defend): a revisited id
    terminates the walk and logs the chain.
    """
    by_id = _index_by_id(msgs)
    if head_id not in by_id:
        return []

    chain: list[MessageLike] = []
    seen: set[str] = set()
    cur_id: Optional[str] = head_id
    while cur_id and cur_id in by_id and cur_id not in seen:
        seen.add(cur_id)
        cur = by_id[cur_id]
        chain.append(cur)
        cur_id = _parent_of(cur)
    chain.reverse()
    return chain


def is_ancestor(
    msgs: list[MessageLike], anc_id: str, desc_id: str,
) -> bool:
    """Is ``anc_id`` reachable from ``desc_id`` via ``called_by``?"""
    if anc_id == desc_id:
        return True
    by_id = _index_by_id(msgs)
    desc = by_id.get(desc_id)
    cur: Optional[str] = _parent_of(desc) if desc else None
    seen: set[str] = set()
    while cur and cur not in seen:
        if cur == anc_id:
            return True
        seen.add(cur)
        cur_msg = by_id.get(cur)
        if cur_msg is None:
            break
        cur = _parent_of(cur_msg)
    return False


def normalize_parent_pointers(msgs: list[MessageLike]) -> None:
    """Backfill ``called_by`` on legacy messages (in place).

    Conversations created before the DAG store may lack ``called_by``.
    Treat the list as a straight chain: each message's parent is
    the one before it. Messages that already have ``called_by`` are
    left alone.
    """
    prev_id: Optional[str] = None
    for m in msgs:
        if "called_by" not in m:
            if isinstance(m, dict):
                m["called_by"] = prev_id
        prev_id = m.get("id") or prev_id


def advance_head(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv['messages']`` and move HEAD to it.

    If the message has no ``called_by``, set it to the current HEAD.
    An explicit ``None`` is respected (root-level fork).
    """
    if "called_by" not in msg:
        msg["called_by"] = conv.get("head_id")
    conv.setdefault("messages", []).append(msg)
    if msg.get("id"):
        conv["head_id"] = msg["id"]


def deepest_leaf(msgs: list[MessageLike], msg_id: str) -> str:
    """Walk down children from ``msg_id`` to the deepest leaf.

    When there are multiple children, pick the most recent one
    (highest ``created_at``, insertion-order tiebreaker).
    """
    by_id = _index_by_id(msgs)
    cur_id: Optional[str] = msg_id
    seen: set[str] = set()
    while cur_id and cur_id in by_id and cur_id not in seen:
        seen.add(cur_id)
        kids = children(msgs, cur_id)
        if not kids:
            return cur_id
        cur_id = kids[-1].get("id")
    return msg_id


def head_or_tip(conv: dict, msgs: list[MessageLike]) -> Optional[str]:
    """Return ``conv['head_id']`` if set; otherwise the last message's id."""
    head = conv.get("head_id")
    if head:
        return head
    if not msgs:
        return None
    return msgs[-1].get("id")
