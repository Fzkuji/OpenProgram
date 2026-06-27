"""Pure DAG helpers over a list of message dicts.

Each message links to its parent via ``called_by`` (preferred) or
``parent_id`` (legacy fallback). The helper ``_parent_of(m)`` reads
both, so all traversal works with old and new data alike.

Contract:

* ``siblings(msgs, msg_id)`` — messages sharing a parent with
  ``msg_id``, including ``msg_id`` itself.
* ``children(msgs, msg_id)`` — messages whose parent is ``msg_id``.
* ``linear_history(msgs, head_id)`` — walk parent pointers from
  ``head_id`` back to the root, return list in root-first order.
* ``is_ancestor(msgs, anc_id, desc_id)`` — whether ``anc_id`` is
  reachable from ``desc_id`` via parent pointers.
* ``normalize_parent_pointers(msgs)`` — migration helper for legacy
  conversations without parent pointers.
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

    Prefers ``called_by`` (new DAG store) over ``parent_id`` (legacy).
    Returns None for root nodes (called_by=ROOT counts as root).
    """
    cb = m.get("called_by")
    if cb and cb != "ROOT":
        return cb
    pid = m.get("parent_id")
    if pid and pid != "ROOT":
        return pid
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
    """Walk from ``head_id`` back to the root along parent pointers.

    Returns messages in root-first order. Uses ``_parent_of`` which
    prefers ``called_by`` over ``parent_id``.

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
    """Is ``anc_id`` reachable from ``desc_id`` via parent pointers?

    Used by checkout validation when we want to confirm a proposed
    new head is actually on the same tree (usually we don't bother —
    any commit in the repo is a valid head — but the helper exists
    for UI affordances like 'branch from ancestor').
    """
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
    """Backfill parent pointers on legacy messages (in place).

    Conversations created before the DAG store may lack both
    ``called_by`` and ``parent_id``. Treat that list as a straight
    chain: each message's parent is the one before it.

    Messages that already carry a parent pointer are left alone.
    """
    prev_id: Optional[str] = None
    for m in msgs:
        has_parent = "called_by" in m or "parent_id" in m
        if not has_parent:
            if isinstance(m, dict):
                m["called_by"] = prev_id
                m["parent_id"] = prev_id
        prev_id = m.get("id") or prev_id


def advance_head(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv['messages']`` and move HEAD to it.

    If the message has no parent pointer (neither ``called_by`` nor
    ``parent_id``), set it to the current HEAD. An explicit ``None``
    is respected (root-level fork).
    """
    if "called_by" not in msg and "parent_id" not in msg:
        msg["called_by"] = conv.get("head_id")
        msg["parent_id"] = conv.get("head_id")
    conv.setdefault("messages", []).append(msg)
    if msg.get("id"):
        conv["head_id"] = msg["id"]


def deepest_leaf(msgs: list[MessageLike], msg_id: str) -> str:
    """Walk down children from ``msg_id`` to the deepest leaf.

    When there are multiple children, pick the most recent one
    (highest ``created_at``, insertion-order tiebreaker). Used by the
    sibling navigator to answer "if I switch to this other branch,
    where should HEAD land?" — always the tip of that branch, not
    the fork point itself. Otherwise users would see an empty branch
    after clicking <.
    """
    by_id = _index_by_id(msgs)
    cur_id: Optional[str] = msg_id
    seen: set[str] = set()
    while cur_id and cur_id in by_id and cur_id not in seen:
        seen.add(cur_id)
        kids = children(msgs, cur_id)
        if not kids:
            return cur_id
        # Latest by timestamp; ties fall back to insertion order
        # inside children() so behavior is deterministic.
        cur_id = kids[-1].get("id")
    return msg_id


def head_or_tip(conv: dict, msgs: list[MessageLike]) -> Optional[str]:
    """Return ``conv['head_id']`` if set; otherwise the last message's id.

    Callers use this to decide what to display for conversations loaded
    from disk that pre-date the ``head_id`` field.
    """
    head = conv.get("head_id")
    if head:
        return head
    if not msgs:
        return None
    return msgs[-1].get("id")
