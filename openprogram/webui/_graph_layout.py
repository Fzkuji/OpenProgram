"""Backend DAG layout for the history-graph panel.

Frontend (web/lib/legacy/history-graph.ts) used to compute both
``_depth`` (row / y) and ``_lane`` (column / x) from the flat message
list. The "depth = list index" heuristic put retry siblings on
sequential rows so they read as staircased nesting instead of true
parallel branches, and the lane code had its own off-by-one issues.

This module does the layout server-side from the real DAG topology:

  * **depth**: tree-depth from the root. Siblings (same parent_id)
    share a depth, so retry branches that fork off one user turn all
    sit on the same y-row and their children stay aligned across
    branches.
  * **lane**: each leaf claims one column. Trunk leaf (the chain
    ending at HEAD) = lane 0. Other leaves get lanes 1, 2, … in
    created_at order. A non-leaf node inherits its leaf's lane.

The returned graph entries gain ``_depth`` and ``_lane`` fields. The
frontend already has fallback logic for the legacy "no backend
layout" shape, so this is purely additive.
"""
from __future__ import annotations

from typing import Iterable, Optional


def annotate_graph(graph_entries: list[dict], head_id: Optional[str]) -> list[dict]:
    """Add ``_depth`` + ``_lane`` to every entry in ``graph_entries``.

    Returns the same list (mutated). Entries without ``id`` are
    skipped — they wouldn't render anyway.
    """
    by_id: dict[str, dict] = {m["id"]: m for m in graph_entries if m.get("id")}
    children: dict[str, list[str]] = {}
    for m in by_id.values():
        pid = m.get("parent_id")
        if pid and pid in by_id:
            children.setdefault(pid, []).append(m["id"])
    # SessionDB writes the wall-clock under ``timestamp``; some
    # graph-build paths normalize that to ``created_at``. Honour
    # whichever is present so sort order survives both shapes.
    def _ts(nid: str) -> float:
        m = by_id.get(nid) or {}
        return float(m.get("created_at") or m.get("timestamp") or 0)

    # Children sort: by created_at ascending so older retries appear
    # first (= leftmost columns).
    for pid in children:
        children[pid].sort(key=_ts)

    # ── depth (tree depth from root, with tools stacked) ─────────
    # Plain tree-depth would put every tool child of one assistant
    # at the same row, dropping 10 of them onto one point. Tools
    # are sequential within their parent, so stack them: each tool
    # bumps the depth so subsequent tools (and any non-tool sibling
    # that comes after) sit below it.
    roots = [
        m["id"] for m in by_id.values()
        if not m.get("parent_id") or m.get("parent_id") not in by_id
    ]
    depth: dict[str, int] = {}

    def _walk(nid: str, start_depth: int) -> int:
        """Visit ``nid`` at ``start_depth`` and recurse.

        Returns the last row occupied by this subtree so an outer
        chronological successor (a sibling tool, etc.) can stack
        below it.

        Tree-depth model with one twist for tools:
          * non-tool children all sit at ``start_depth + 1``
            (siblings share a row → parallel branches),
          * tool children each occupy their own row, stacked
            chronologically starting at ``start_depth + 1`` so a
            multi-tool assistant turn reads vertically instead of
            piling every tool on the same point.
        """
        if nid in depth:
            return start_depth
        depth[nid] = start_depth
        kids = children.get(nid, [])
        last_row = start_depth
        # First handle non-tool children — they all sit on the same
        # row (start_depth + 1). Their own subtrees may extend the
        # last_row counter independently.
        non_tool_row = start_depth + 1
        for cid in kids:
            if by_id.get(cid, {}).get("role") == "tool":
                continue
            sub_last = _walk(cid, non_tool_row)
            if sub_last > last_row:
                last_row = sub_last
        # Now stack tool children chronologically. First tool starts
        # at start_depth + 1 (same level as non-tools, but its own
        # column via lane); subsequent tools at last_row + 1.
        first_tool = True
        for cid in kids:
            if by_id.get(cid, {}).get("role") != "tool":
                continue
            tool_row = (start_depth + 1) if first_tool else last_row + 1
            first_tool = False
            sub_last = _walk(cid, tool_row)
            if sub_last > last_row:
                last_row = sub_last
        return last_row

    for rid in sorted(
        roots,
        key=lambda x: _ts(x),
    ):
        _walk(rid, 0)

    # ── tier (call-stack depth) ──────────────────────────────────
    # Distinct from ``_depth``: depth is the visual y-row that
    # respects tool stacking, tier is "how many nesting levels deep
    # am I in the call stack" — a logical concept the renderer uses
    # to taper node size / opacity for sub-calls. Every parent →
    # child edge bumps tier by 1, regardless of whether the child is
    # a sibling tool or a true nested call.
    tier: dict[str, int] = {}

    def _walk_tier(nid: str, t: int) -> None:
        if nid in tier:
            return
        tier[nid] = t
        for cid in children.get(nid, []):
            _walk_tier(cid, t + 1)

    for rid in sorted(roots, key=lambda x: _ts(x)):
        _walk_tier(rid, 0)

    # ── lane (leaf-based column) ──────────────────────────────────
    # Leaves are the branch tips after we strip tool attachments —
    # a node is a leaf if none of its children (transitively, through
    # tool children) is a user/assistant. Without this every
    # assistant with tool calls would be denied "leaf" status and
    # the only leaves would be tool rows, which then each grab their
    # own lane.
    def _has_non_tool_descendant(nid: str) -> bool:
        stack = list(children.get(nid, []))
        seen: set[str] = set()
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            if by_id.get(cid, {}).get("role") != "tool":
                return True
            stack.extend(children.get(cid, []))
        return False

    leaves = [
        nid for nid in by_id
        if by_id[nid].get("role") != "tool"
        and not _has_non_tool_descendant(nid)
    ]
    leaf_lane: dict[str, int] = {}
    trunk_leaf: Optional[str] = None
    if head_id and head_id in by_id:
        # If head landed on a tool row (worker restart mid-turn etc.)
        # walk up to the assistant that owns the tool first.
        cur = head_id
        hops = 0
        while cur and cur in by_id and by_id[cur].get("role") == "tool" and hops < 50:
            cur = by_id[cur].get("parent_id")
            hops += 1
        if cur and cur in by_id:
            trunk_leaf = _descend_to_leaf(cur, children, by_id)
    if trunk_leaf and trunk_leaf in by_id and trunk_leaf in {*leaves}:
        leaf_lane[trunk_leaf] = 0
    else:
        # trunk_leaf isn't a (non-tool) leaf — pick the most recent
        # leaf instead so the active branch still gets lane 0.
        trunk_leaf = max(leaves, key=lambda lid: _ts(lid)) if leaves else None
        if trunk_leaf:
            leaf_lane[trunk_leaf] = 0
    other_leaves = sorted(
        (lid for lid in leaves if lid != trunk_leaf),
        key=lambda lid: _ts(lid),
    )
    for i, lid in enumerate(other_leaves):
        leaf_lane[lid] = i + 1

    # Map each node to a leaf (then to a lane).
    # Greedy: walk every leaf back up the parent chain, claiming
    # ancestors that haven't been claimed yet.
    leaf_of: dict[str, str] = {}
    # Trunk first so it owns the shared trunk chain.
    walk_order: list[str] = []
    if trunk_leaf:
        walk_order.append(trunk_leaf)
    walk_order.extend(other_leaves)
    for leaf in walk_order:
        cur: Optional[str] = leaf
        while cur and cur not in leaf_of:
            leaf_of[cur] = leaf
            cur = by_id[cur].get("parent_id") if cur in by_id else None
            if cur not in by_id:
                cur = None
    # Any orphan: maps to itself, lane 0.
    for nid in by_id:
        if nid not in leaf_of:
            leaf_of[nid] = nid

    # Now project lane.
    lane: dict[str, int] = {}
    for nid in by_id:
        lid = leaf_of.get(nid, nid)
        lane[nid] = leaf_lane.get(lid, 0)
    # Tool rows that didn't claim a leaf inherit their parent's lane
    # so they stay on the same column as the assistant they belong
    # to. Walk up the parent chain until we find a lane.
    for nid, node in by_id.items():
        if node.get("role") != "tool":
            continue
        cur = node.get("parent_id")
        hops = 0
        while cur and cur in by_id and hops < 50:
            if cur in lane:
                lane[nid] = lane[cur]
                break
            cur = by_id[cur].get("parent_id")
            hops += 1

    # ── annotate ──────────────────────────────────────────────────
    for m in graph_entries:
        nid = m.get("id")
        if not nid:
            continue
        m["_depth"] = depth.get(nid, 0)
        m["_lane"] = lane.get(nid, 0)
        m["_tier"] = tier.get(nid, 0)
    return graph_entries


def _descend_to_leaf(
    start: str,
    children: dict[str, list[str]],
    by_id: dict[str, dict],
) -> str:
    """Walk to a leaf descendant of ``start``, preferring non-tool
    children. Returns ``start`` when it has no non-tool descendants.
    """
    cur = start
    while True:
        kids = children.get(cur) or []
        non_tool = [k for k in kids if by_id.get(k, {}).get("role") != "tool"]
        if not non_tool:
            return cur
        cur = non_tool[-1]
