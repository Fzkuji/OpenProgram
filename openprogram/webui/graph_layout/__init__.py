"""DAG layout pipeline. See README.md for stage breakdown.

Public entry point:

    annotate_graph(graph_entries, head_id) -> graph_entries

Reads ``parent_id`` (conv edge) + ``caller`` (sub-call edge) from each
entry, writes ``_depth`` / ``_lane`` / ``_tier`` back into the same
dicts. Returns the same list (mutated) — except entries filtered out
in stage 1 (microcompact noise), which are dropped from the result so
the frontend never sees them.
"""
from __future__ import annotations

from typing import Optional

from .filter import filter_visible
from .topology import build_children
from .tier import compute_tier
from .depth import compute_depth
from .lane import compute_lane
from .reflow import reflow_overlaps


def annotate_graph(
    graph_entries: list[dict],
    head_id: Optional[str],
) -> list[dict]:
    visible = filter_visible(graph_entries)
    by_id: dict[str, dict] = {m["id"]: m for m in visible}

    conv_children, call_children = build_children(by_id)
    tier = compute_tier(by_id)
    depth = compute_depth(by_id, call_children)
    lane, alloc = compute_lane(by_id, conv_children, head_id)

    # Adaptive overlap resolution — push colliding sub-call clusters
    # to fresh lanes. Runs after the initial assignment so it sees
    # the full picture.
    reflow_overlaps(by_id, lane, depth, tier, call_children, alloc)

    for m in visible:
        nid = m["id"]
        m["_depth"] = depth.get(nid, 0)
        m["_lane"] = lane.get(nid, 0)
        m["_tier"] = tier.get(nid, 0)
    return visible
