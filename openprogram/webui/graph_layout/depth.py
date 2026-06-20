"""Depth (row index) per node.

ROOT (display=root) is the only true root, depth=0.
DFS pre-order from ROOT over call_children.
Fork branches (no called_by, found via parent_id) start at the
same depth as the first sibling at the fork point.
"""
from __future__ import annotations

from ._common import ts, parent_id_of, is_root


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
) -> dict[str, float]:
    depth: dict[str, float] = {}
    counter = [0]

    def _walk(nid: str, start_depth: "float | None" = None) -> None:
        if nid in depth:
            return
        if start_depth is not None:
            depth[nid] = start_depth
            counter[0] = max(counter[0], int(start_depth) + 1)
        else:
            depth[nid] = counter[0]
            counter[0] += 1
        for kid in call_children.get(nid, []):
            _walk(kid)

    # Only display=root nodes are true roots
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
        _walk(r)

    # Fork branches: nodes without called_by that have a parent_id.
    # Position at the same depth as the first sibling at that fork.
    remaining = sorted(
        (nid for nid in by_id if nid not in depth),
        key=lambda x: ts(by_id, x),
    )
    for nid in remaining:
        if nid in depth:
            continue
        m = by_id[nid]
        pid = parent_id_of(by_id, m)
        if pid and pid in depth:
            first_sibling_depth = None
            for other_nid, other_m in by_id.items():
                if other_nid == nid:
                    continue
                if parent_id_of(by_id, other_m) == pid and other_nid in depth:
                    first_sibling_depth = depth[other_nid]
                    break
            if first_sibling_depth is not None:
                _walk(nid, first_sibling_depth)
            else:
                _walk(nid, depth[pid] + 1)
        else:
            _walk(nid)

    return depth
