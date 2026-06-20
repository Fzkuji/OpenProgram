"""Lane (column) assignment for the session DAG.

Strategy:
  * ROOT and its first child chain get lane 0 (the trunk).
  * Fork detection via parent_id: when multiple nodes share the same
    parent_id, the first (by created_at) keeps the parent's lane,
    the rest each get a fresh lane.
  * All called_by descendants of a node inherit its lane.
"""
from __future__ import annotations

from typing import Optional

from ._common import called_by_of, parent_id_of, is_root, ts


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
    # First = earliest by created_at among nodes sharing the same parent_id.
    first_at_fork: set[str] = set()
    for pid, kids in fork_siblings.items():
        if kids:
            first_at_fork.add(kids[0])

    def _walk(nid: str, my_lane: int) -> None:
        if nid in lane:
            return
        lane[nid] = my_lane

        # Walk called_by children. Each child inherits our lane
        # UNLESS it's a fork sibling (shares parent_id with another
        # node) and not the first — then it gets a new lane.
        for kid in call_children.get(nid, []):
            kid_m = by_id.get(kid, {})
            kid_pid = parent_id_of(by_id, kid_m)

            # Check if this kid is part of a fork
            if kid_pid and kid_pid in fork_siblings:
                siblings = fork_siblings[kid_pid]
                if len(siblings) > 1 and kid not in first_at_fork:
                    _walk(kid, alloc.alloc())
                    continue
            _walk(kid, my_lane)

    # Start from ROOT nodes (no called_by, no caller)
    roots = sorted(
        (nid for nid, m in by_id.items()
         if not called_by_of(by_id, m)),
        key=lambda x: ts(by_id, x),
    )
    for r in roots:
        _walk(r, alloc.alloc())

    # Catch any unvisited nodes
    for nid in by_id:
        if nid not in lane:
            lane[nid] = 0

    return lane, alloc
