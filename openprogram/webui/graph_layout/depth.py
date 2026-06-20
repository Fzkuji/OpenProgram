"""Depth (row index) per node — DFS pre-order over the called_by tree.

ROOT gets depth 0. Each subsequent node in DFS order gets depth+1.
This produces the vertical ordering seen in the tree-style view.
"""
from __future__ import annotations

from ._common import ts


def compute_depth(
    by_id: dict[str, dict],
    call_children: dict[str, list[str]],
) -> dict[str, float]:
    depth: dict[str, float] = {}
    counter = [0]

    def _walk(nid: str) -> None:
        if nid in depth:
            return
        depth[nid] = counter[0]
        counter[0] += 1
        for kid in call_children.get(nid, []):
            _walk(kid)

    roots = sorted(
        (nid for nid, m in by_id.items()
         if not m.get("called_by") and not m.get("caller")),
        key=lambda x: ts(by_id, x),
    )
    for r in roots:
        _walk(r)
    for nid in sorted(by_id.keys(), key=lambda x: ts(by_id, x)):
        if nid not in depth:
            _walk(nid)
    return depth
