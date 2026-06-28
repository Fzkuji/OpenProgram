"""Depth (row index) per node.

Main trunk: DFS pre-order with a global counter — each node gets the
next row. Fork branches start at the same row as their first sibling
and their subtrees grow downward independently.
"""
from __future__ import annotations

from ._common import ts, called_by_of, is_root


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
    fork_siblings: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    depth: dict[str, float] = {}
    counter = [0]

    # Which nodes are the first sibling at each fork point.
    first_at_fork: set[str] = set()
    if fork_siblings:
        for _pid, kids in fork_siblings.items():
            if len(kids) > 1:
                first_at_fork.add(kids[0])

    def _walk_dfs(nid: str) -> None:
        if nid in depth:
            return
        depth[nid] = counter[0]
        counter[0] += 1
        for kid in call_children.get(nid, []):
            if fork_siblings and kid not in first_at_fork:
                # Check if this kid is a non-first fork sibling
                pid = called_by_of(by_id, by_id.get(kid, {}))
                if pid and fork_siblings.get(pid) and len(fork_siblings[pid]) > 1 and kid != fork_siblings[pid][0]:
                    continue
            _walk_dfs(kid)

    def _walk_branch(nid: str, start: float) -> None:
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
        pid = called_by_of(by_id, m)
        start = 0
        if pid and pid in depth:
            for other_nid, other_m in by_id.items():
                if other_nid == nid:
                    continue
                if called_by_of(by_id, other_m) == pid and other_nid in depth:
                    start = depth[other_nid]
                    break
            else:
                start = depth[pid] + 1
        _walk_branch(nid, start)

    return depth
