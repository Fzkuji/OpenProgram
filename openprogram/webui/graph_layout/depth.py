"""Depth (row index) per node.

Main trunk: DFS pre-order with a global counter — each node gets the
next row. Fork branches start at the same row as their first sibling
and their subtrees grow downward independently.
"""
from __future__ import annotations

from ._common import ts, parent_id_of, is_root


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
) -> dict[str, float]:
    depth: dict[str, float] = {}
    counter = [0]

    def _walk_dfs(nid: str) -> None:
        """DFS walk using global counter — for the main trunk."""
        if nid in depth:
            return
        depth[nid] = counter[0]
        counter[0] += 1
        for kid in call_children.get(nid, []):
            _walk_dfs(kid)

    def _walk_branch(nid: str, start: float) -> None:
        """Walk a branch subtree starting at a fixed depth, then
        incrementing for each called_by child."""
        if nid in depth:
            return
        depth[nid] = start
        d = start + 1
        for kid in call_children.get(nid, []):
            if kid not in depth:
                depth[kid] = d
                d += 1
                for grandkid in call_children.get(kid, []):
                    _walk_branch(grandkid, depth[kid] + 1)

    # Main trunk: DFS from ROOT
    roots = sorted(
        (nid for nid, m in by_id.items() if is_root(m)),
        key=lambda x: ts(by_id, x),
    )
    if not roots:
        roots = sorted(
            (nid for nid, m in by_id.items()
             if not m.get("called_by") and not m.get("caller")),
            key=lambda x: ts(by_id, x),
        )
    for r in roots:
        _walk_dfs(r)

    # Fork branches: align with first sibling, grow independently
    remaining = sorted(
        (nid for nid in by_id if nid not in depth),
        key=lambda x: ts(by_id, x),
    )
    for nid in remaining:
        if nid in depth:
            continue
        m = by_id[nid]
        pid = parent_id_of(by_id, m)
        start = 0
        if pid and pid in depth:
            # Find the first sibling's depth
            for other_nid, other_m in by_id.items():
                if other_nid == nid:
                    continue
                if parent_id_of(by_id, other_m) == pid and other_nid in depth:
                    start = depth[other_nid]
                    break
            else:
                start = depth[pid] + 1
        _walk_branch(nid, start)

    return depth
