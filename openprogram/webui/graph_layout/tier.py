"""Tier (horizontal indent level) per node.

Every called_by hop adds +1 tier. ROOT=0, user=1, llm=2, tool=3.
This gives the tree-indent staircase in the design spec.
"""
from __future__ import annotations

from ._common import called_by_of


def compute_tier(by_id: dict[str, dict]) -> dict[str, int]:
    tier: dict[str, int] = {}

    def _t(nid: str, depth: int = 0) -> int:
        if nid in tier:
            return tier[nid]
        if depth > 200:
            tier[nid] = 0
            return 0
        m = by_id[nid]
        ca = called_by_of(by_id, m)
        if ca:
            t = _t(ca, depth + 1) + 1
        else:
            t = 0
        tier[nid] = t
        return t

    for nid in by_id:
        _t(nid)
    return tier
