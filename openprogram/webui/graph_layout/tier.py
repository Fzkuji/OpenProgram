"""Tier (horizontal indent level) per node.

Tier = depth in the **call tree**, not the conversation chain.
Conversation-level nodes cycle between fixed tiers:
  ROOT=0, user=1, llm=2, next user=1, next llm=2, ...
A top-level node hanging directly off ROOT is ONE unit from root
(tier 1), whether it's a chat user message or a manual function call —
uniform spacing is the contract.
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
        # spawn 分支根：对话层节点（dag-rendering.md 第一节裁决）。它的
        # caller 指向发起 spawn 的那轮，但它开启的是新分支的对话，tier
        # 按对话层 user=1 计，不吃 caller 的执行层缩进。
        if m.get("source") == "agent_spawn" and not m.get("predecessor"):
            tier[nid] = 1
            return 1
        # caller field = sub-call parent (tool invoked by llm)
        caller = m.get("caller") or ""
        parent_node = by_id.get(caller) if caller else None
        if parent_node and not is_root(parent_node):
            t = _t(caller, depth + 1) + 1
            tier[nid] = t
            return t
        # Top-level (hangs off ROOT / no in-graph caller): tier by role.
        # A user turn is one unit from root (1), its llm reply the next
        # (2). A manual function call hanging directly off ROOT is ALSO
        # just one unit from root (1) — same as a chat message — and its
        # own sub-calls indent from it via the caller recursion above.
        role = m.get("role", "")
        if role in ("assistant", "llm"):
            tier[nid] = 2
        else:  # user, tool, code, or anything else at top level
            tier[nid] = 1
        return tier[nid]

    for nid in by_id:
        _t(nid)
    return tier
