"""DAG layout pipeline. See README.md for stage breakdown.

Public entry point:

    annotate_graph(graph_entries, head_id) -> graph_entries

Reads ``called_by`` (conv edge) + ``caller`` (sub-call edge) from each
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
    depth = compute_depth(by_id, call_children)
    lane, alloc = compute_lane(by_id, call_children, fork_siblings, head_id)

    # Spread lanes so each branch has room for its tier width.
    # lane=0 with max_tier=1 occupies columns 0-1; lane=1 starts at 2.
    lane_groups: dict[int, int] = {}  # lane_id → max tier in that lane
    for nid, ln in lane.items():
        t = tier.get(nid, 0)
        lane_groups[ln] = max(lane_groups.get(ln, 0), t)
    sorted_lanes = sorted(lane_groups.keys())
    lane_offset: dict[int, int] = {}
    col = 0
    for ln in sorted_lanes:
        lane_offset[ln] = col
        col += lane_groups[ln] + 1  # +1 for the lane's own column
    for nid in lane:
        lane[nid] = lane_offset.get(lane[nid], lane[nid])

    for m in visible:
        nid = m["id"]
        m["_depth"] = depth.get(nid, 0)
        m["_lane"] = lane.get(nid, 0)
        m["_tier"] = tier.get(nid, 0)
    return visible
