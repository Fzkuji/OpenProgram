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

from .filter import filter_visible, normalize_followup
from .topology import build_children
from .tier import compute_tier
from .depth import compute_depth
from .lane import compute_lane
from .reflow import reflow_overlaps


def annotate_graph(
    graph_entries: list[dict],
    head_id: Optional[str],
) -> list[dict]:
    # Normalize must run before filter — it rewrites the followup
    # reply's parent_id to point at the attach pointer, so the
    # subsequent filter can safely drop the synthetic [系统消息]
    # user msg without orphaning the reply.
    normalize_followup(graph_entries)
    visible = filter_visible(graph_entries)
    by_id: dict[str, dict] = {m["id"]: m for m in visible}

    conv_children, call_children = build_children(by_id)
    tier = compute_tier(by_id)
    depth = compute_depth(by_id, call_children, conv_children)
    lane, alloc = compute_lane(by_id, conv_children, head_id)

    # Adaptive overlap resolution — push colliding sub-call clusters
    # to fresh lanes. Runs after the initial assignment so it sees
    # the full picture.
    reflow_overlaps(by_id, lane, depth, tier, call_children, alloc)

    # Spawn alignment — sub-branches (source=agent_spawn) with no
    # caller / parent have an orphan conv-root that depth.py puts at
    # row 0, so the sub lane starts at the very top of the figure
    # instead of "below the spawn point". Find each such root, walk
    # back to the task tool that produced it via the attach pointer,
    # and shift the whole sub lane down so its first node sits one
    # row below the task tool. Also nudge its lane right so the sub
    # branch doesn't share a column with a sibling tool call (which
    # would otherwise stack vertically on the same x).
    _align_spawned_branches(by_id, depth, lane, tier, conv_children, call_children)

    for m in visible:
        nid = m["id"]
        m["_depth"] = depth.get(nid, 0)
        m["_lane"] = lane.get(nid, 0)
        m["_tier"] = tier.get(nid, 0)
    return visible


def _align_spawned_branches(
    by_id: dict[str, dict],
    depth: dict[str, float],
    lane: dict[str, int],
    tier: dict[str, int],
    conv_children: dict[str, list[str]],
    call_children: dict[str, list[str]],
) -> None:
    """Shift each spawned sub-branch's rows down so its first node
    sits right below the task tool call that spawned it.

    For each ``source=agent_spawn`` conv root, find the associated
    ``function=task`` tool node by walking the sub-branch tip back
    through the attach pointer; compute the desired row offset and
    apply it across the entire sub-tree (conv + call descendants).
    """
    # Build attach lookup: tip head_id → attach pointer node
    attach_by_tip: dict[str, dict] = {}
    for n in by_id.values():
        if (n.get("function") != "attach"):
            continue
        ah = _attach_head_id(n)
        if ah:
            attach_by_tip[ah] = n
    # Identify spawn roots
    spawn_roots: list[dict] = [
        n for n in by_id.values()
        if (n.get("source") == "agent_spawn"
            and not (n.get("parent_id") or "").strip()
            and not (n.get("caller") or n.get("called_by") or "").strip())
    ]
    for root in spawn_roots:
        # Walk the spawn tree's conv chain to find the tip.
        tip = _walk_to_tip(root["id"], by_id, conv_children)
        if not tip:
            continue
        # Find an attach pointer that references this sub-branch's
        # tip — its conv parent (the spawning reply) is what we
        # anchor against.
        attach = attach_by_tip.get(tip)
        if not attach:
            # Try walking up the tip's caller chain (some sub-agents
            # finish on a tool node, so tip via conv may not match
            # what attach captured).
            continue
        anchor_id = attach.get("parent_id") or attach.get("caller")
        if not anchor_id or anchor_id not in by_id:
            continue
        # Find the task tool on the anchor's call children.
        task_node = None
        for cid in call_children.get(anchor_id, []):
            cn = by_id.get(cid) or {}
            if cn.get("function") == "task":
                task_node = cn
                break
        if task_node is None:
            continue
        # Lane alignment — push the sub branch right so its column
        # doesn't collide with sibling tool calls on the spawning
        # turn. Effective column of a node = lane + tier. The sub
        # branch's column must be > the max effective column of
        # the anchor's call children, otherwise tool stacks would
        # overlap the sub branch vertically.
        max_sibling_col = 0
        for sibling_id in call_children.get(anchor_id, []):
            scol = lane.get(sibling_id, 0) + tier.get(sibling_id, 0)
            if scol > max_sibling_col:
                max_sibling_col = scol
        # Anchor's own column counts too — never let sub branch land
        # to the left of the anchor.
        max_sibling_col = max(
            max_sibling_col,
            lane.get(anchor_id, 0) + tier.get(anchor_id, 0),
        )
        target_lane = max_sibling_col + 1
        root_lane = lane.get(root["id"], 0)
        lane_delta = target_lane - root_lane
        if lane_delta > 0:
            stack = [root["id"]]
            seen0: set[str] = set()
            while stack:
                nid = stack.pop()
                if nid in seen0:
                    continue
                seen0.add(nid)
                lane[nid] = lane.get(nid, 0) + lane_delta
                for cid in conv_children.get(nid, []):
                    stack.append(cid)
                for cid in call_children.get(nid, []):
                    stack.append(cid)

        task_depth = depth.get(task_node["id"], 0)
        root_depth = depth.get(root["id"], 0)
        # Sub-branch root sits exactly one row below the task tool —
        # same step as any other parent → child relationship in the
        # figure. Anything larger reads as a mystery gap.
        target_root_depth = task_depth + 1
        delta = target_root_depth - root_depth
        if delta > 0:
            # Apply delta to root + all descendants (conv + call).
            stack = [root["id"]]
            seen: set[str] = set()
            while stack:
                nid = stack.pop()
                if nid in seen:
                    continue
                seen.add(nid)
                depth[nid] = depth.get(nid, 0) + delta
                for cid in conv_children.get(nid, []):
                    stack.append(cid)
                for cid in call_children.get(nid, []):
                    stack.append(cid)

        # Time-order on main: attach (and anything below it) must
        # come AFTER the sub-branch finishes. Shift attach + its
        # conv subtree so attach.depth = sub_tip.depth + 1.
        sub_tip_id = _walk_to_tip(root["id"], by_id, conv_children)
        sub_tip_depth = depth.get(sub_tip_id, depth.get(root["id"], 0))
        attach_depth = depth.get(attach["id"], 0)
        target_attach_depth = sub_tip_depth + 1
        a_delta = target_attach_depth - attach_depth
        if a_delta > 0:
            stack = [attach["id"]]
            seen2: set[str] = set()
            while stack:
                nid = stack.pop()
                if nid in seen2:
                    continue
                seen2.add(nid)
                depth[nid] = depth.get(nid, 0) + a_delta
                for cid in conv_children.get(nid, []):
                    stack.append(cid)
                for cid in call_children.get(nid, []):
                    stack.append(cid)


def _attach_head_id(node: dict) -> str:
    """Pull the source-branch tip id from an attach node's metadata.
    Handles both the top-level ``attach_ref`` shortcut (set by
    ws_actions builders) and the nested ``extra.attach.head_id``."""
    v = node.get("attach_ref")
    if isinstance(v, str) and v.strip():
        return v.strip()
    extra = node.get("extra")
    if isinstance(extra, str) and extra:
        try:
            import json as _json
            parsed = _json.loads(extra)
            a = parsed.get("attach") or {}
            h = a.get("head_id")
            if isinstance(h, str) and h.strip():
                return h.strip()
        except Exception:
            pass
    return ""


def _walk_to_tip(
    root_id: str,
    by_id: dict[str, dict],
    conv_children: dict[str, list[str]],
) -> str:
    """Follow conv children down from ``root_id`` until a node with
    no further conv children — the leaf of the spawned sub-branch."""
    cur = root_id
    hops = 0
    while hops < 1000:
        hops += 1
        kids = conv_children.get(cur, [])
        if not kids:
            return cur
        # Take the latest by creation if multiple
        cur = sorted(
            kids,
            key=lambda x: (by_id.get(x) or {}).get("created_at") or 0,
            reverse=True,
        )[0]
    return cur
