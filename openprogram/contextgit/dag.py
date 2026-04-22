"""Pure DAG helpers over a list of message dicts.

Each message is expected to have at minimum ``id`` and ``parent_id``
(nullable for root messages). No persistence, no streaming — this
module is called by the server and the CLI both, and must stay pure so
tests can drive it with plain dicts.

Contract:

* ``siblings(msgs, msg_id)`` — messages sharing a parent with
  ``msg_id``, including ``msg_id`` itself. Returned in ``created_at``
  order (or insertion order if timestamps are absent).
* ``children(msgs, msg_id)`` — messages whose ``parent_id`` is
  ``msg_id``.
* ``linear_history(msgs, head_id)`` — walk parent pointers from
  ``head_id`` back to the root, return list in root-first order.
* ``is_ancestor(msgs, anc_id, desc_id)`` — whether ``anc_id`` is
  reachable from ``desc_id`` via parent pointers.
* ``normalize_parent_pointers(msgs)`` — migration helper. For legacy
  conversations without ``parent_id``, chain each message to its
  predecessor in ``msgs`` order so old data behaves as a straight
  linear DAG.
* ``head_or_tip(conv, msgs)`` — return the conversation's ``head_id``
  if set; otherwise the last message's id (tip of the linear chain).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol


class MessageLike(Protocol):
    """Duck type for the dicts we operate on. Nothing else matters."""

    def __getitem__(self, key: str) -> Any: ...
    def get(self, key: str, default: Any = ...) -> Any: ...


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

    Root messages (``parent_id is None``) are siblings of all other
    root messages. Unknown ``msg_id`` returns ``[]``.
    """
    by_id = _index_by_id(msgs)
    target = by_id.get(msg_id)
    if target is None:
        return []
    parent_id = target.get("parent_id")
    return _sorted_by_created_at(
        m for m in msgs if m.get("parent_id") == parent_id
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
    """Messages whose ``parent_id`` is ``msg_id``, ordered by creation."""
    return _sorted_by_created_at(
        m for m in msgs if m.get("parent_id") == msg_id
    )


def linear_history(msgs: list[MessageLike], head_id: str) -> list[MessageLike]:
    """Walk from ``head_id`` back to the root along ``parent_id``.

    Returns messages in root-first order. Each step picks the *exact*
    parent — if you want to choose among siblings mid-walk, do that by
    setting the conversation's head to the sibling you want first.

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
        cur_id = cur.get("parent_id")
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
    cur: Optional[str] = by_id.get(desc_id, {}).get("parent_id") if by_id.get(desc_id) else None
    seen: set[str] = set()
    while cur and cur not in seen:
        if cur == anc_id:
            return True
        seen.add(cur)
        cur_msg = by_id.get(cur)
        if cur_msg is None:
            break
        cur = cur_msg.get("parent_id")
    return False


def normalize_parent_pointers(msgs: list[MessageLike]) -> None:
    """Backfill ``parent_id`` on legacy messages (in place).

    Conversations created before ContextGit don't have ``parent_id``.
    Treat that list as a straight chain: each message's parent is the
    one before it (first message has ``parent_id = None``).

    Messages that already carry an explicit ``parent_id`` are left
    alone — that way re-normalizing a partially-migrated list is a
    no-op.
    """
    prev_id: Optional[str] = None
    for m in msgs:
        # Some callers pass dataclass-like objects, but today they're
        # dicts. Only assign if the attribute is absent OR explicitly
        # None AND we haven't already set it. Explicit None is the
        # pre-migration default — treat it as "needs fill".
        has_parent = "parent_id" in m and m.get("parent_id") is not None
        if not has_parent:
            if isinstance(m, dict):
                m["parent_id"] = prev_id
        prev_id = m.get("id") or prev_id


def advance_head(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv['messages']`` and move HEAD to it.

    Semantics:

    * ``"parent_id" not in msg`` — caller didn't specify one. Parent
      to the current HEAD (normal "extend the tip" path).
    * ``msg["parent_id"]`` is present (even if ``None``) — trust the
      caller verbatim. ``None`` is a legitimate value for root-level
      siblings (retrying the first turn of a conversation forks a new
      root), and must NOT be silently rewritten to HEAD — doing so
      collapses the fork into a linear append and breaks the DAG.

    After the append HEAD == ``msg['id']``. Callers persist via
    ``_save_conversation`` separately — we don't do I/O here.
    """
    if "parent_id" not in msg:
        msg["parent_id"] = conv.get("head_id")
    conv.setdefault("messages", []).append(msg)
    if msg.get("id"):
        conv["head_id"] = msg["id"]


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
