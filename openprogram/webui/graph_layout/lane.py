"""Lane (column) assignment for the session DAG.

Strategy:
  * ROOT and its first child chain get lane 0 (the trunk).
  * Fork detection via called_by: when multiple nodes share the same
    called_by, the first (by created_at) keeps the parent's lane,
    the rest each get a fresh lane.
  * All called_by descendants of a node inherit its lane.
"""
from __future__ import annotations

from typing import Optional

from ._common import called_by_of, called_by_of, is_root, ts


class LaneAllocator:
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
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]],
    head_id: Optional[str] = None,
) -> tuple[dict[str, int], LaneAllocator]:
    lane: dict[str, int] = {}
    alloc = LaneAllocator()

    # Which nodes are the "first" sibling at each fork point.
    # First = earliest by created_at among nodes sharing the same called_by.
    first_at_fork: set[str] = set()
    for pid, kids in fork_siblings.items():
        if kids:
            first_at_fork.add(kids[0])

    def _walk(nid: str, my_lane: int) -> None:
        if nid in lane:
            return
        lane[nid] = my_lane

        for kid in call_children.get(nid, []):
            if kid in first_at_fork:
                _walk(kid, my_lane)
            else:
                _walk(kid, alloc.alloc())

    # Start from ROOT (display=root) only
    from ._common import is_root
    roots = sorted(
        (nid for nid, m in by_id.items() if is_root(m)),
        key=lambda x: ts(by_id, x),
    )
    if not roots:
        roots = sorted(
            (nid for nid, m in by_id.items()
             if not called_by_of(by_id, m)),
            key=lambda x: ts(by_id, x),
        )
    for r in roots:
        _walk(r, alloc.alloc())

    # Fork branches without called_by: assign fresh lanes
    remaining = sorted(
        (nid for nid in by_id if nid not in lane),
        key=lambda x: ts(by_id, x),
    )
    for nid in remaining:
        if nid not in lane:
            _walk(nid, alloc.alloc())

    return lane, alloc
