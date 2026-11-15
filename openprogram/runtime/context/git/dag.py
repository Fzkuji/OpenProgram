"""Pure DAG helpers over a list of message dicts.

Each message links to its parent via ``predecessor``.

Contract:

* ``siblings(msgs, msg_id)`` — messages sharing a parent with
  ``msg_id``, including ``msg_id`` itself.
* ``children(msgs, msg_id)`` — messages whose parent is ``msg_id``.
* ``linear_history(msgs, head_id)`` — walk ``predecessor`` from
  ``head_id`` back to the root, return list in root-first order.
* ``is_ancestor(msgs, anc_id, desc_id)`` — whether ``anc_id`` is
  reachable from ``desc_id`` via ``predecessor``.
* ``normalize_parent_pointers(msgs)`` — migration helper for legacy
  conversations without ``predecessor``.
* ``head_or_tip(conv, msgs)`` — return the conversation's ``head_id``
  if set; otherwise the last message's id.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol, Sequence


class MessageLike(Protocol):
    """Duck type for the dicts we operate on. Nothing else matters."""

    def __getitem__(self, key: str) -> Any: ...
    def get(self, key: str, default: Any = ...) -> Any: ...


def _parent_of(m: MessageLike) -> Optional[str]:
    """Return the parent pointer of a message node.

    Uses ``predecessor`` exclusively. Returns None for root nodes
    (predecessor=ROOT or empty/missing).
    """
    cb = m.get("predecessor")
    if cb and cb != "ROOT":
        return cb
    return None


def _index_by_id(msgs: Iterable[MessageLike]) -> dict[str, MessageLike]:
    return {m["id"]: m for m in msgs if m.get("id")}


def _sorted_by_created_at(items: Iterable[MessageLike]) -> list[MessageLike]:
    """Stable sort by ``created_at``; missing timestamps sort last in
    insertion order. We preserve insertion order as the tiebreaker so
    legacy messages without timestamps still render deterministically."""
    positioned = list(enumerate(items))
    positioned.sort(key=lambda row: (row[1].get("created_at") or 0, row[0]))
    return [message for _position, message in positioned]


def _sibling_key(m: MessageLike) -> tuple:
    """Grouping key for the chat ``< N/M >`` sibling nav.

    Plain predecessor-sharing is NOT enough: sub-call tool rows and
    spawned-branch roots have no ``predecessor`` at all, so they all
    collapsed into one giant "root sibling" set with the first user
    turn (the "1/6 branches on a fresh 你好" bug). Three refinements:

    * parent falls back to ``caller`` (a spawned branch's root
      expresses its fork point via caller, not predecessor);
    * a spawned branch (``source == "agent_spawn"``) never mixes with
      organic turns, whether it starts at a new root or forks off a
      turn — it's a branch an agent or /task opened, not an
      alternative the user can page to.
    """
    parent = m.get("predecessor") or m.get("caller") or None
    if parent == "ROOT":
        parent = None
    return (parent, m.get("source") == "agent_spawn")


def _is_chat_lane(m: MessageLike) -> bool:
    """Nodes eligible for chat sibling nav: conversation turns only —
    no sub-call tool/code rows, no ROOT marker, no runtime cards
    (fn-run / attach pointers get fn-run-scoped nav from the caller),
    no compaction summaries (one shares the predecessor of the first row
    it covers, and HEAD can never stand on it)."""
    if m.get("role") in ("tool", "code"):
        return False
    if m.get("id") == "ROOT" or m.get("display") == "root":
        return False
    if m.get("display") == "runtime":
        return False
    if m.get("covers_ids") is not None:
        return False
    return True


def _is_side_row(m: MessageLike) -> bool:
    """Rows hung off a turn without continuing it: attach pointers,
    runtime placeholders and compaction summaries."""
    if m.get("function") == "attach":
        return True
    if m.get("type") == "status" and m.get("display") == "runtime":
        return True
    return m.get("covers_ids") is not None


def _dead_end_side_rows(msgs: Iterable[MessageLike]) -> set[str]:
    """Side rows no ordinary row continues from, where a walk to a branch
    tip must not stop. Older rows can hang off a side row, because a version
    arrow could once leave HEAD on an attach pointer, so a side row with an
    ordinary child stays on the path to that child."""
    rows = list(msgs)
    continued = {_parent_of(m) for m in rows if not _is_side_row(m)}
    return {
        m["id"] for m in rows
        if m.get("id") and _is_side_row(m) and m["id"] not in continued
    }


def siblings(msgs: list[MessageLike], msg_id: str) -> list[MessageLike]:
    """Return messages sharing a fork point with ``msg_id`` (includes
    itself) — the set the chat ``< N/M >`` switcher pages through.

    Unknown ``msg_id`` returns ``[]``. Sub-call tool rows never form
    sibling sets (their nav is fn-run-scoped, handled by the caller).
    """
    by_id = _index_by_id(msgs)
    target = by_id.get(msg_id)
    if target is None:
        return []
    if not _is_chat_lane(target):
        return [target]
    key = _sibling_key(target)
    return _sorted_by_created_at(
        m for m in msgs if _is_chat_lane(m) and _sibling_key(m) == key
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


def sibling_navigation_index(
    msgs: list[MessageLike],
    *,
    target_ids: Optional[Iterable[str]] = None,
) -> dict[str, tuple[int, int, Optional[str], Optional[str]]]:
    """Build chat sibling navigation fields with one message scan.

    The tuple values are ``(index, total, previous_leaf, next_leaf)``.
    ``target_ids`` limits sorting and result materialization to nodes the
    caller will render; all messages are still grouped once so totals and
    neighboring leaves remain authoritative. Non-chat targets retain the
    existing singleton result.
    """
    by_id = _index_by_id(msgs)
    targets = (
        set(by_id)
        if target_ids is None
        else {message_id for message_id in target_ids if message_id in by_id}
    )
    navigation = {
        message_id: (1, 1, None, None)
        for message_id in targets
    }
    targets_by_group: dict[tuple, list[str]] = {}
    for message_id in targets:
        target = by_id[message_id]
        if _is_chat_lane(target):
            targets_by_group.setdefault(
                _sibling_key(target), [],
            ).append(message_id)
    if not targets_by_group:
        return navigation

    groups = {key: [] for key in targets_by_group}
    for message in msgs:
        if not _is_chat_lane(message):
            continue
        key = _sibling_key(message)
        if key in groups:
            groups[key].append(message)

    pending: list[
        tuple[str, int, int, Optional[str], Optional[str]]
    ] = []
    needs_deepest_leaf = False
    for key, requested_ids in targets_by_group.items():
        ordered_ids = [
            message.get("id") for message in _sorted_by_created_at(groups[key])
        ]
        first_position: dict[str, int] = {}
        for position, message_id in enumerate(ordered_ids):
            if message_id:
                first_position.setdefault(message_id, position)
        total = len(ordered_ids)
        for message_id in requested_ids:
            position = first_position.get(message_id)
            if position is None:
                continue
            previous_id = ordered_ids[position - 1] if position > 0 else None
            following_id = (
                ordered_ids[position + 1] if position < total - 1 else None
            )
            needs_deepest_leaf |= previous_id is not None or following_id is not None
            pending.append(
                (message_id, position + 1, total, previous_id, following_id)
            )

    latest_child: dict[str, tuple[tuple[object, int], Optional[str]]] = {}
    if needs_deepest_leaf:
        dead_ends = _dead_end_side_rows(msgs)
        for position, message in enumerate(msgs):
            parent = _parent_of(message)
            if parent is None or message.get("id") in dead_ends:
                continue
            score = (message.get("created_at") or 0, position)
            current = latest_child.get(parent)
            if current is None or score >= current[0]:
                latest_child[parent] = (score, message.get("id"))

    deepest_cache: dict[str, str] = {}

    def indexed_deepest_leaf(message_id: Optional[str]) -> Optional[str]:
        if message_id is None:
            return None
        if message_id in deepest_cache:
            return deepest_cache[message_id]

        start = message_id
        current: Optional[str] = message_id
        path: list[str] = []
        seen: set[str] = set()
        while current and current in by_id and current not in seen:
            if current in deepest_cache:
                leaf = deepest_cache[current]
                for path_id in path:
                    deepest_cache[path_id] = leaf
                return leaf
            seen.add(current)
            path.append(current)
            child = latest_child.get(current)
            if child is None:
                for path_id in path:
                    deepest_cache[path_id] = current
                return current
            current = child[1]

        # Preserve ``deepest_leaf`` for malformed cycles or missing child ids:
        # it terminates and returns the original query id.
        return start

    for message_id, index, total, previous_id, following_id in pending:
        navigation[message_id] = (
            index,
            total,
            indexed_deepest_leaf(previous_id),
            indexed_deepest_leaf(following_id),
        )
    return navigation


def children(msgs: list[MessageLike], msg_id: str) -> list[MessageLike]:
    """Messages whose parent is ``msg_id``, ordered by creation."""
    return _sorted_by_created_at(
        m for m in msgs if _parent_of(m) == msg_id
    )


def linear_history(msgs: list[MessageLike], head_id: str) -> list[MessageLike]:
    """Walk from ``head_id`` back to the root along ``predecessor``.

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


def active_branch_chain(
    all_msgs: list[MessageLike],
    branch_ids: Sequence[str],
    head: Optional[str],
) -> list[MessageLike]:
    """The chat display list for ONE branch: only the active branch's
    nodes, root-first, with tool_calls already folded onto parents.

    ``all_msgs`` is the full, tool-aggregated node set (every branch).
    ``branch_ids`` is the active branch's ids in the order of
    ``SessionDB.get_branch(head)`` — the authoritative answer to which
    nodes are on this branch and in what order. Storage order is not that
    order: a reply placeholder is written before a steer that then
    becomes its predecessor.

    Why look rows up in ``all_msgs`` rather than return ``get_branch``
    directly: ``get_branch`` walks parent-ward from the head (an
    assistant/user node) and never descends into tool nodes (which hang
    off ``caller``, not the conv path), so its rows carry no folded
    ``tool_calls``. ``all_msgs`` has already folded each tool row into its
    parent assistant, and that assistant is on the branch, so its row
    keeps its tool calls.

    Fallbacks (never blank the page):
    - ``branch_ids`` empty (stale/None head after a mid-turn crash) →
      walk ``head``'s predecessor chain over ``all_msgs``.
    - no ``head`` at all → return ``all_msgs`` unchanged.
    """
    if branch_ids:
        by_id = _index_by_id(all_msgs)
        ordered = [by_id[node_id] for node_id in branch_ids if node_id in by_id]
        if ordered:
            return ordered
    if head:
        return linear_history(all_msgs, head)
    return list(all_msgs)


def is_ancestor(
    msgs: list[MessageLike], anc_id: str, desc_id: str,
) -> bool:
    """Is ``anc_id`` reachable from ``desc_id`` via ``predecessor``?"""
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
    """Backfill ``predecessor`` on legacy messages (in place).

    Conversations created before the DAG store may lack ``predecessor``.
    Treat the list as a straight chain: each message's parent is
    the one before it. Messages that already have ``predecessor`` are
    left alone.
    """
    prev_id: Optional[str] = None
    for m in msgs:
        if "predecessor" not in m:
            if isinstance(m, dict):
                m["predecessor"] = prev_id
        prev_id = m.get("id") or prev_id


def advance_head(conv: dict, msg: dict) -> None:
    """Append ``msg`` to ``conv['messages']`` and move HEAD to it.

    If the message has no ``predecessor``, set it to the current HEAD.
    An explicit ``None`` is respected (root-level fork).
    """
    if "predecessor" not in msg:
        msg["predecessor"] = conv.get("head_id")
    conv.setdefault("messages", []).append(msg)
    if msg.get("id"):
        conv["head_id"] = msg["id"]


def deepest_leaf(msgs: list[MessageLike], msg_id: str) -> str:
    """Walk down children from ``msg_id`` to the deepest leaf.

    When there are multiple children, pick the most recent one
    (highest ``created_at``, insertion-order tiebreaker). The walk never
    ends on an attach pointer, runtime placeholder or compaction summary,
    which HEAD must not stand on.
    """
    by_id = _index_by_id(msgs)
    dead_ends = _dead_end_side_rows(msgs)
    cur_id: Optional[str] = msg_id
    seen: set[str] = set()
    while cur_id and cur_id in by_id and cur_id not in seen:
        seen.add(cur_id)
        kids = [k for k in children(msgs, cur_id) if k.get("id") not in dead_ends]
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
