"""y-row assignment — ``_depth`` per node.

Conv chain: each conv-child is +1 row below its parent (integer
depths). Sub-call cluster: each k-th sub-call sits at
``caller.depth + 1 + k * SUBCALL_STEP`` so long stacks (30 read()
calls in one turn) don't dominate the panel with 30 full rows.
``SUBCALL_STEP`` < 1 compresses vertically; conv chains stay
integer-aligned so they read like a normal timeline.
"""
from __future__ import annotations

from ._common import caller_of, conv_parent_of

# Vertical spacing factor for sub-call siblings. 0.7 means a stack
# of N tools spans ~0.7*N rows instead of N. Tested at 0.7 — tighter
# (0.5) starts to look cramped at typical NODE_R.
SUBCALL_STEP = 0.7


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
) -> dict[str, float]:
    depth: dict[str, float] = {}

    def _d(nid: str, recursion: int = 0) -> float:
        if nid in depth:
            return depth[nid]
        if recursion > 200:
            depth[nid] = 0
            return 0
        m = by_id[nid]
        cp = conv_parent_of(by_id, m)
        if cp:
            d: float = _d(cp, recursion + 1) + 1
        else:
            ca = caller_of(by_id, m)
            if ca:
                siblings = call_children.get(ca, [])
                try:
                    k = siblings.index(nid)
                except ValueError:
                    k = 0
                d = _d(ca, recursion + 1) + 1 + k * SUBCALL_STEP
            else:
                d = 0
        depth[nid] = d
        return d

    for nid in by_id:
        _d(nid)
    return depth
