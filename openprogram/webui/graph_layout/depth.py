"""Depth (row index) per node.

Main trunk: DFS pre-order with a global counter — each node gets the
next row. Fork branches start at the same row as their first sibling
and their subtrees grow downward independently.
"""
from __future__ import annotations

from ._common import ts, predecessor_of, is_root


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    depth: dict[str, float] = {}
    counter = [0]

    # Non-first fork siblings — skipped during DFS so they land in
    # the "remaining" pass and align with the first sibling's depth.
    _skip_in_dfs: set[str] = set()
    if fork_siblings:
        for _pid, kids in fork_siblings.items():
            if len(kids) > 1:
                for k in kids[1:]:
                    _skip_in_dfs.add(k)

    def _walk_dfs(nid: str) -> None:
        if nid in depth:
            return
        depth[nid] = counter[0]
        counter[0] += 1
        for kid in call_children.get(nid, []):
            if kid in _skip_in_dfs:
                continue
            _walk_dfs(kid)

    def _walk_branch(nid: str, start: float) -> None:
        """Walk a branch subtree starting at a fixed depth, then
        incrementing for each predecessor child."""
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
             if not m.get("predecessor") and not m.get("caller")),
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
        pid = predecessor_of(by_id, m)
        start = 0
        if pid and pid in depth:
            # Find the first sibling's depth
            for other_nid, other_m in by_id.items():
                if other_nid == nid:
                    continue
                if predecessor_of(by_id, other_m) == pid and other_nid in depth:
                    start = depth[other_nid]
                    break
            else:
                start = depth[pid] + 1
        _walk_branch(nid, start)

    return depth
