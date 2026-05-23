"""x-column assignment — ``_lane`` per node.

Strategy:
  * Trunk leaf (head_id's conv-root) → lane 0.
  * Other conv-roots / retry-siblings → fresh lanes (1, 2, …).
  * Sub-call kids inherit caller's lane.

Walking the conv tree from each root: the conv-child that contains
head stays on the same lane; siblings each claim a new lane via
``_alloc()``. Sub-calls run a final pass — they piggy-back on their
caller's lane.

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
    head_id: Optional[str],
) -> tuple[dict[str, int], LaneAllocator]:
    lane: dict[str, int] = {}
    alloc = LaneAllocator()

    # Conv-roots = nodes with no conv parent AND no caller (i.e.
    # genuine roots, not sub-calls whose caller was pruned).
    conv_roots = [
        nid for nid, m in by_id.items()
        if not conv_parent_of(by_id, m) and not caller_of(by_id, m)
    ]

    trunk_root = _conv_root_of(by_id, head_id) if head_id and head_id in by_id else None
    if trunk_root and trunk_root in conv_roots:
        # Move trunk to the front so it claims lane 0.
        conv_roots.remove(trunk_root)
        conv_roots.insert(0, trunk_root)
    # Other roots sorted by created_at for deterministic ordering.
    conv_roots[1:] = sorted(conv_roots[1:], key=lambda x: ts(by_id, x))

    def _subtree_contains(root: str, target: str) -> bool:
        if root == target:
            return True
        stack = list(conv_children.get(root, []))
        seen: set[str] = set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x == target:
                return True
            stack.extend(conv_children.get(x, []))
        return False

    def _walk(nid: str, my_lane: int) -> None:
        if nid in lane:
            return
        lane[nid] = my_lane
        kids = conv_children.get(nid, [])
        if not kids:
            return
        # Pick the kid leading to head as "primary" — it keeps the
        # current lane. Siblings each get a fresh lane allocated.
        primary = kids[0]
        if head_id:
            for k in kids:
                if _subtree_contains(k, head_id):
                    primary = k
                    break
        for k in kids:
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

    return lane, alloc


def _conv_root_of(by_id: dict[str, dict], nid: str) -> Optional[str]:
    """Walk up both edges to reach the conv-root that owns ``nid``."""
    cur: Optional[str] = nid
    hops = 0
    seen: set[str] = set()
    while cur and cur in by_id and cur not in seen and hops < 200:
        seen.add(cur)
        hops += 1
        m = by_id[cur]
        ca = caller_of(by_id, m)
        if ca:
            cur = ca
            continue
        cp = conv_parent_of(by_id, m)
        if not cp:
            return cur
        cur = cp
    return cur
