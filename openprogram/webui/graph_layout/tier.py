"""Call-stack depth — ``_tier`` per node.

Walking conv edges keeps tier the same (peer relation: user ↔
assistant on the main thread). Walking the caller edge bumps tier
+1 (sub-call goes one level deeper). A tool spawning a sub-LLM goes
tier+2 from the outer assistant.
"""
from __future__ import annotations

from ._common import caller_of, conv_parent_of


def compute_tier(by_id: dict[str, dict]) -> dict[str, int]:
    tier: dict[str, int] = {}

    def _t(nid: str, depth: int = 0) -> int:
        if nid in tier:
            return tier[nid]
        if depth > 200:
            tier[nid] = 0
            return 0
        m = by_id[nid]
        ca = caller_of(by_id, m)
        if ca:
            t = _t(ca, depth + 1) + 1
        else:
            cp = conv_parent_of(by_id, m)
            t = _t(cp, depth + 1) if cp else 0
        tier[nid] = t
        return t

    for nid in by_id:
        _t(nid)
    return tier
