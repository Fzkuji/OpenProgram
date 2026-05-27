"""x-column assignment — ``_lane`` per node.

Lane assignment is **stable** — the same DAG always produces the
same lane layout regardless of which branch is currently active.
Switching ``head_id`` between branches doesn't shuffle the figure
around (which would make visual comparison impossible).

Strategy:
  * Conv-roots sorted by ``created_at`` (earliest first → lane 0).
  * Within a node's children: first child by ``created_at`` keeps
    the parent's lane (the "trunk-following" lane), siblings each
    claim a fresh lane via ``alloc()``.
  * Sub-call kids inherit their caller's lane in a final pass.

The ``head_id`` argument is accepted but ignored for layout — kept
in the signature so callers don't need to change. (Visual
highlighting of the active branch lives on the renderer, not in
the layout coordinates.)

Reflow (``reflow.py``) may reassign sub-call lanes afterwards to
resolve overlaps, so this stage only computes the initial mapping.
"""
from __future__ import annotations

from typing import Optional

from ._common import caller_of, conv_parent_of, ts


class LaneAllocator:
    """Single-source-of-truth for "next free lane index".

    Both initial assignment (here) and reflow (post-pass) draw from
    the same counter so they never reuse the same lane number.
    """
    def __init__(self) -> None:
        self._next = 0

    def alloc(self) -> int:
        i = self._next
        self._next += 1
        return i

    @property
    def used(self) -> int:
        return self._next


def compute_lane(
    by_id: dict[str, dict],
    conv_children: dict[str, list[str]],
    head_id: Optional[str] = None,   # accepted but ignored — see module docstring
) -> tuple[dict[str, int], LaneAllocator]:
    lane: dict[str, int] = {}
    alloc = LaneAllocator()
    del head_id  # explicit: layout never branches on the active head

    # Conv-roots = nodes with no conv parent AND no caller (i.e.
    # genuine roots, not sub-calls whose caller was pruned). Sorted
    # by created_at so the figure is stable across reloads + branch
    # switches.
    conv_roots = sorted(
        (
            nid for nid, m in by_id.items()
            if not conv_parent_of(by_id, m) and not caller_of(by_id, m)
        ),
        key=lambda x: ts(by_id, x),
    )

    def _walk(nid: str, my_lane: int) -> None:
        if nid in lane:
            return
        lane[nid] = my_lane
        kids = conv_children.get(nid, [])
        if not kids:
            return
        # If ANY child is a /task spawn (function="task"), every child
        # forks to a fresh lane — the parent trunk stops here. Same as
        # git: `git checkout -b X` puts the new commit on a new ref,
        # leaving main pointing at the old commit. Without this rule
        # the trunk visually swallows the spawned sub-agent's turn
        # and the lane-0 "main line" extends into territory written
        # by some other agent.
        spawn_fork = any(
            (by_id.get(k, {}).get("function")) == "task"
            for k in kids
        )
        if spawn_fork:
            for k in kids:
                _walk(k, alloc.alloc())
            return
        # Regular conv-tree fork: pick primary = first child that is
        # NOT a sub-agent spawn (``source=agent_spawn``). Sub-agent
        # user msgs (created when /task --async fires) often land
        # earliest by seq and would otherwise claim kids[0] and pull
        # the trunk into the sub-agent's lane — leaving the caller's
        # real continuation (followup turn, future user messages) on
        # a side lane. With this skip the trunk stays on the caller's
        # main thread, matching the list_branches walk's main-lane
        # detection, and the attach pointer (which piggy-backs on the
        # caller user msg's lane via called_by) lands on main too.
        # Attach pointers always stay on the caller's lane — they
        # represent the spawn event on the main thread, not a fork.
        # Their dashed-edge child (sub-agent lane) will pick up a
        # fresh lane via the agent_spawn branch.
        primary = None
        for k in kids:
            if (by_id.get(k, {}).get("source")) == "agent_spawn":
                continue
            primary = k
            break
        if primary is None:
            primary = kids[0]
        for k in kids:
            kn = by_id.get(k, {})
            if kn.get("function") == "attach":
                _walk(k, my_lane)
            else:
                _walk(k, my_lane if k == primary else alloc.alloc())

    for root in conv_roots:
        if root not in lane:
            _walk(root, alloc.alloc())

    # Sub-calls piggy-back on caller's lane. Walk the caller chain
    # until we hit a node that already has a lane.
    for nid, m in by_id.items():
        if nid in lane:
            continue
        cur = caller_of(by_id, m)
        hops = 0
        seen: set[str] = set()
        while cur and cur in by_id and cur not in seen and hops < 200:
            if cur in lane:
                lane[nid] = lane[cur]
                break
            seen.add(cur)
            hops += 1
            cur = caller_of(by_id, by_id[cur])
        lane.setdefault(nid, 0)

    # Branch-referencing function_calls (attach / merge) sit on
    # their caller's lane as sequence-level nodes — their own conv
    # children (e.g. the auto-followup reply re-parented onto the
    # attach pointer) need to be walked too. The initial _walk only
    # descends from conv_roots; attach/merge nodes aren't roots
    # (they have a non-empty caller), so this extra pass picks up
    # the chain that hangs off them.
    for nid, m in by_id.items():
        if m.get("function") not in ("attach", "merge"):
            continue
        if nid not in lane:
            continue
        for k in conv_children.get(nid, []):
            if k not in lane:
                _walk(k, lane[nid])

    return lane, alloc
