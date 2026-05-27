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

# Vertical spacing factor for sub-call siblings. 1.0 keeps the figure
# strictly on the integer grid — every node sits on a row that's a
# multiple of ROW_H — so siblings line up visually with conv chain
# nodes. We previously used 0.7 to compress long tool stacks, but it
# made the figure look "jittery" because adjacent lanes ended up half
# a row off from each other.
SUBCALL_STEP = 1.0


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    conv_children: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    depth: dict[str, float] = {}
    conv_children = conv_children or {}

    def _d(nid: str, recursion: int = 0) -> float:
        if nid in depth:
            return depth[nid]
        if recursion > 200:
            depth[nid] = 0
            return 0
        m = by_id[nid]
        # Branch-referencing function_calls (attach / merge) live on
        # the caller's branch as sequence nodes. They must sit AFTER
        # the caller's LLM reply (which shares the same parent_id as
        # the caller in some schemas → same depth as caller+1), so
        # we anchor below the max depth among caller's conv children,
        # not just caller.depth+1. Otherwise attach and the reply
        # collapse onto the same row.
        if m.get("function") in ("attach", "merge"):
            ca = caller_of(by_id, m)
            anchor = ca or m.get("parent_id")
            if anchor and anchor in by_id and anchor != nid:
                base = _d(anchor, recursion + 1)
                # Max depth of anchor's existing conv-tree subtree —
                # walk shallow children only, to avoid runaway recursion
                # in cyclic data.
                kids = [k for k in conv_children.get(anchor, []) if k != nid]
                kid_max = base
                for k in kids:
                    if k in by_id:
                        kid_max = max(kid_max, _d(k, recursion + 1))
                d: float = max(base, kid_max) + 1
            else:
                d = 0
            depth[nid] = d
            return d
        cp = conv_parent_of(by_id, m)
        if cp:
            d = _d(cp, recursion + 1) + 1
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
