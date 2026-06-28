"""DAG layout pipeline. See README.md for stage breakdown.

Public entry point:

    annotate_graph(graph_entries, head_id) -> graph_entries

Reads ``predecessor`` (conv edge) + ``caller`` (sub-call edge) from each
entry, writes ``_depth`` / ``_lane`` / ``_tier`` back into the same
dicts. Returns the same list (mutated) — except entries filtered out
in stage 1 (microcompact noise), which are dropped from the result so
the frontend never sees them.
"""
from __future__ import annotations

from typing import Optional

from .filter import filter_visible, normalize_followup
from .topology import build_children
from .tier import compute_tier
from .depth import compute_depth
from .lane import compute_lane


def annotate_graph(
    graph_entries: list[dict],
    head_id: Optional[str],
) -> list[dict]:
    normalize_followup(graph_entries)
    visible = filter_visible(graph_entries)
    by_id: dict[str, dict] = {m["id"]: m for m in visible}

    call_children, fork_siblings = build_children(by_id)
    tier = compute_tier(by_id)
    depth = compute_depth(by_id, call_children, fork_siblings)
    lane, alloc = compute_lane(by_id, call_children, fork_siblings, head_id)

    # Column offset per lane. A fork lane starts ONE column right of the
    # ENTIRE base lane it diverged from — i.e. right of the base lane's
    # rightmost occupied column (its deepest node, sub-tree included), so
    # the two branches never overlap. Collapsed sub-calls don't exist in
    # ``tier``, so they take no column — the fork packs tight against
    # what's actually visible.
    from ._common import predecessor_of

    # nodes grouped by lane; first node = the lane's earliest (by depth).
    lane_nodes: dict[int, list[str]] = {}
    lane_first: dict[int, str] = {}
    for nid, ln in lane.items():
        lane_nodes.setdefault(ln, []).append(nid)
        cur = lane_first.get(ln)
        if cur is None or depth.get(nid, 0) < depth.get(cur, 0):
            lane_first[ln] = nid

    lane_offset: dict[int, int] = {}

    def _rightmost_col(ln: int) -> int:
        """Rightmost occupied column of a lane (offset + max tier)."""
        base = _offset(ln)
        return base + max((tier.get(n, 0) for n in lane_nodes.get(ln, [])), default=0)

    def _offset(ln: int) -> int:
        if ln in lane_offset:
            return lane_offset[ln]
        if ln == 0:
            lane_offset[ln] = 0
            return 0
        first = lane_first.get(ln)
        forked_from = predecessor_of(by_id, by_id[first]) if first else None
        # base lane = the lane the fork diverged from.
        base_lane = lane.get(forked_from) if forked_from else None
        first_tier = tier.get(first, 0) if first else 0
        if base_lane is not None:
            # fork's first node goes one column right of the base lane's
            # rightmost column → offset = that + 1 - first node's tier.
            off = _rightmost_col(base_lane) + 1 - first_tier
        else:
            off = max(lane_offset.values(), default=0) + 1
        lane_offset[ln] = off
        return off

    for ln in sorted(set(lane.values())):
        _offset(ln)
    for nid in lane:
        lane[nid] = lane_offset.get(lane[nid], lane[nid])

    for m in visible:
        nid = m["id"]
        m["_depth"] = depth.get(nid, 0)
        m["_lane"] = lane.get(nid, 0)
        m["_tier"] = tier.get(nid, 0)
    return visible
