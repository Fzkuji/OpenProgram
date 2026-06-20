"""Tier (horizontal indent level) per node.

ROOT = 0. ROOT's direct children (user nodes) = 0 (same column as ROOT).
Each further called_by hop adds +1: user→llm = tier 1, llm→tool = tier 2.
"""
from __future__ import annotations

from ._common import called_by_of, is_root


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
            parent_tier = _t(ca, depth + 1)
            # ROOT→child: same tier (organizational, not a sub-call)
            if is_root(by_id.get(ca, {})):
                t = parent_tier
            else:
                t = parent_tier + 1
        else:
            t = 0
        tier[nid] = t
        return t

    for nid in by_id:
        _t(nid)
    return tier
