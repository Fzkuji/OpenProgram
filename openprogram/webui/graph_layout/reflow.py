"""Adaptive overlap resolution for sub-call clusters.

Initial layout puts every sub-call cluster on its caller's lane,
offset right by ``tier``. Two callers on the same lane both running
30 tool calls produce two clusters at the same ``(lane, tier=1)``
column — visually one column-wide strip that spans two non-adjacent
depth ranges. If the ranges overlap (the second cluster starts before
the first one ends, in depth-units), the nodes draw on top of each
other.

This stage detects that collision in (effective_x, depth) space and
pushes the colliding (later-starting) cluster to a fresh lane. We
preserve the cluster's tier — only the lane number changes — so the
sub-call still reads as "this is one level deeper than its caller".

Picking the *later* cluster to move keeps the first cluster anchored
on its caller's column (more intuitive: the visually closer cluster
stays attached).
"""
from __future__ import annotations

from .lane import LaneAllocator


def effective_x(lane: int, tier: int) -> tuple[int, int]:
    """(lane, tier) makes the visual column key. Two clusters share
    a column iff their (lane, tier) match."""
    return (lane, tier)


def reflow_overlaps(
    by_id: dict[str, dict],
    lane: dict[str, int],
    depth: dict[str, float],
    tier: dict[str, int],
    call_children: dict[str, list[str]],
    alloc: LaneAllocator,
) -> None:
    """Mutate ``lane`` to resolve overlapping sub-call clusters.

    Algorithm:
      1. For each caller with sub-calls, compute the cluster's depth
         range + effective column (lane, tier).
      2. Group clusters by column. Walk each group in depth order;
         track the last-cluster's max depth. If the next cluster's
         min depth < last_max → collision.
      3. Re-lane the colliding cluster (and every transitive
         descendant of it that shares the same lane) to a fresh
         lane from ``alloc``.
    """
    clusters: list[tuple[int, int, float, float, str, list[str]]] = []
    for caller_id, kids in call_children.items():
        if not kids:
            continue
        if caller_id not in lane:
            # Caller pruned (e.g. by filter step) — skip its cluster.
            continue
        # Cluster's tier = its kids' tier (all the same, kid_tier =
        # caller.tier + 1). Cluster's lane = kids' shared lane.
        kid_tier = tier.get(kids[0], 0)
        kid_lane = lane.get(kids[0], 0)
        dmin = min(depth.get(k, 0) for k in kids)
        dmax = max(depth.get(k, 0) for k in kids)
        clusters.append((kid_lane, kid_tier, dmin, dmax, caller_id, kids))

    # Group clusters by column.
    by_col: dict[tuple[int, int], list[tuple[float, float, str, list[str]]]] = {}
    for ln, tr, dmin, dmax, cid, kids in clusters:
        by_col.setdefault((ln, tr), []).append((dmin, dmax, cid, kids))

    for col, items in by_col.items():
        items.sort(key=lambda x: x[0])  # by depth_min
        last_max: float = -1.0
        for i, (dmin, dmax, cid, kids) in enumerate(items):
            if i == 0 or dmin >= last_max:
                last_max = max(last_max, dmax)
                continue
            # Collision — push this cluster (and any descendants
            # sharing its current lane) to a fresh lane.
            new_lane = alloc.alloc()
            _relane_descendants(
                by_id, lane, call_children, kids, col[0], new_lane,
            )
            # The reflowed cluster's depth range is unchanged but its
            # column is now different, so last_max stays bound to the
            # prior cluster on the original column.

    return None


def _relane_descendants(
    by_id: dict[str, dict],
    lane: dict[str, int],
    call_children: dict[str, list[str]],
    seeds: list[str],
    old_lane: int,
    new_lane: int,
) -> None:
    """Set ``lane`` to ``new_lane`` for every seed and every
    sub-call descendant that currently sits on ``old_lane``. Stops
    at the lane boundary so we don't disturb peers that already
    found their place elsewhere via earlier reflow.
    """
    stack = list(seeds)
    seen: set[str] = set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        if lane.get(nid) != old_lane:
            continue
        lane[nid] = new_lane
        # Recurse into sub-call children (tools may spawn sub-LLMs).
        stack.extend(call_children.get(nid, []))
