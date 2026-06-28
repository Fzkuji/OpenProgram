"""Tier (horizontal indent level) per node.

Tier = depth in the **call tree**, not the conversation chain.
Conversation-level nodes cycle between fixed tiers:
  ROOT=0, user=1, llm=2, next user=1, next llm=2, ...
Sub-call nodes (tool/code) indent from their caller:
  llm→code=3, code→llm=4, code→code=4, etc.
"""
from __future__ import annotations

from ._common import is_root


def compute_tier(by_id: dict[str, dict]) -> dict[str, int]:
    tier: dict[str, int] = {}

    def _t(nid: str, depth: int = 0) -> int:
        if nid in tier:
            return tier[nid]
        if depth > 200:
            tier[nid] = 0
            return 0
        m = by_id[nid]
        if is_root(m):
            tier[nid] = 0
            return 0
        # caller field = sub-call parent (tool invoked by llm)
        caller = m.get("caller") or ""
        parent_node = by_id.get(caller) if caller else None
        if parent_node and not is_root(parent_node):
            t = _t(caller, depth + 1) + 1
            tier[nid] = t
            return t
        # Conversation-level: tier by role
        role = m.get("role", "")
        if role == "user":
            tier[nid] = 1
        elif role in ("assistant", "llm"):
            tier[nid] = 2
        elif role in ("tool", "code"):
            tier[nid] = 3
        else:
            tier[nid] = 1
        return tier[nid]

    for nid in by_id:
        _t(nid)
    return tier
