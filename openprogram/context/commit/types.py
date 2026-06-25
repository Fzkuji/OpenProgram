"""ContextCommit 数据类型 — ContextItem 和 ContextCommit dataclass.

ContextCommit 是某个时刻 LLM 看到的完整 context 的不可变记录。一个
ContextCommit 由一组 ContextItem 组成, 按渲染顺序排列。

设计原则:
  * ContextCommit 一旦生成不可变 (规则升级 → 下个 commit 用新规则, 老的不动)
  * ContextItem.state 只能朝更严的方向走 (full → aged → cleared / summarized)
  * locked=True 的 item 任何规则都不再动
  * summary item 不写 DAG, 它的 source_node_id 是虚拟 id "sm_<hex>"

完整设计见 docs/design/context/context-commit-chain.md。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# ContextItem 的状态机
ItemState = Literal[
    "full",         # 全文进 LLM context (默认)
    "aged",         # 替成语义 stub (tool_aging 干的活)
    "cleared",      # 替成固定占位符 (microcompact 干的活)
    "summarized",   # 已合进某个 summary item, 不渲染
    "summary",      # 这条本身就是合成的 summary item
]

# rendered 长这样的状态
CLEARED_PLACEHOLDER = "[Old tool result content cleared]"


@dataclass
class ContextItem:
    """快照里的一条记录, 对应 LLM 看到的一条消息内容。

    溯源到 DAG 哪个节点 + 当前怎么呈现 + 为什么是这状态。
    """
    # 溯源
    source_node_id: str          # 对应 DAG 节点 id; summary item 用 "sm_<hex>"
    role: str                    # "user" / "assistant" / "tool" / "summary"

    # 当前呈现状态
    state: ItemState = "full"
    locked: bool = False         # True = 状态决定了, 规则跳过

    # 渲染内容 (LLM 实际看到的)
    rendered: str = ""           # full 时 = 原 output; aged/cleared 时 = stub
    tokens: int = 0              # rendered 的 token 估算

    # 决策追溯 (debug + UI 展示用)
    state_set_at: str = ""       # 哪个 context commit 第一次定的这个状态
    reason: str = ""             # "new" / "tail_window" / "idle_60min" / ...
    merged_into: Optional[str] = None  # state=summarized 时, 指向 summary item id

    # 锚点机制 (我们独有的, 非 Claude Code)
    # summary 触发时, 部分高价值节点被选成"锚点", 状态保留 full
    # 而不是变 summarized。这样 LLM 既看到 summary 又看到几个关键原文。
    is_anchor: bool = False
    anchor_for_summary: Optional[str] = None  # 锚点服务的 summary id

    # Attach 溯源
    # 非 None 表示这条 item 是由 attach pointer 展开来的, 值是
    # source 分支末端的 ContextCommit id。用于 (a) UI 按 attach 块
    # 分组渲染, (b) 跨 turn dedup (parent commit 已经有同一个
    # source_commit_id 的 item 就不重复展开).
    attached_from: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_node_id": self.source_node_id,
            "role": self.role,
            "state": self.state,
            "locked": self.locked,
            "rendered": self.rendered,
            "tokens": self.tokens,
            "state_set_at": self.state_set_at,
            "reason": self.reason,
            "merged_into": self.merged_into,
            "is_anchor": self.is_anchor,
            "anchor_for_summary": self.anchor_for_summary,
            "attached_from": self.attached_from,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContextItem":
        return cls(
            source_node_id=d["source_node_id"],
            role=d["role"],
            state=d.get("state", "full"),
            locked=bool(d.get("locked", False)),
            rendered=d.get("rendered", ""),
            tokens=int(d.get("tokens", 0)),
            state_set_at=d.get("state_set_at", ""),
            reason=d.get("reason", ""),
            merged_into=d.get("merged_into"),
            is_anchor=bool(d.get("is_anchor", False)),
            anchor_for_summary=d.get("anchor_for_summary"),
            attached_from=d.get("attached_from"),
        )


@dataclass
class ContextCommit:
    """Context context commit — 某个时刻 LLM 看到的完整 context.

    一旦保存就不可变。改规则只影响下一个 context commit, 不回溯改老 commit。
    """
    id: str                          # commit_<hex>
    session_id: str
    parent_id: Optional[str]         # 兼容字段 = parent_ids[0] if parent_ids else None
    created_at: float
    head_node_id: str                # 对应 DAG 哪个 head
    rules_version: str               # 哪一版规则生成的
    total_tokens: int
    items: list[ContextItem] = field(default_factory=list)
    summary: str = ""                # 这次变化的 1 行人类可读描述
    # merge turn 产生多父 commit; 普通 turn 单父 = [parent_id]
    parent_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 同步 parent_id <-> parent_ids: 谁不空以谁为准, 都给的话 parent_ids 赢
        if self.parent_ids:
            self.parent_id = self.parent_ids[0]
        elif self.parent_id:
            self.parent_ids = [self.parent_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "parent_ids": list(self.parent_ids),
            "created_at": self.created_at,
            "head_node_id": self.head_node_id,
            "rules_version": self.rules_version,
            "total_tokens": self.total_tokens,
            "summary": self.summary,
            "items": [i.to_dict() for i in self.items],
        }


# 当前规则集版本号 — 改任何规则就 bump 这里, 老 context commit 通过
# rules_version 区分。
CURRENT_RULES_VERSION = "v1"
