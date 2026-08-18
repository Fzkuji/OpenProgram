"""Rule protocol — 每个压缩规则一个文件, 实现这个接口.

规则是纯函数风格的 mutator: 拿到一份 items 列表 + 上下文, 就地改 state.

关键约束:
  * 只看 locked=False 的 item, 已 locked 跳过
  * 满足条件就改 state + 设 locked=True (决策一次性, 后续 snap 也尊重)
  * 永远不动 state="pinned" / "summary" / "summarized" 的 item
  * 不能让 state 倒退 (full → aged 可, aged → full 不可)

规则按 RULE_PIPELINE 顺序跑, 顺序固定写死, 不允许动态注入。改顺序
等于改 rules_version, bump 一下。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..commit.types import ContextItem


@dataclass
class RuleContext:
    """规则跑的时候需要知道的环境信息.

    生成器在调每个规则前把当前 snap id / 当前 turn 序号 / token 预算
    / 当前时间 / 调用 LLM 的 callable 都塞进来。
    """
    commit_id: str                         # 这次生成的 snap id (state_set_at 用)
    session_id: str
    now: float                            # 当前 wall-clock
    head_node_id: str
    budget_total: int                     # 模型 context window
    budget_summarize_threshold: int       # 超过这个就触发 summarize
    # 规则需要拉 DAG 节点细节时用 (比如 anchor 选择要看节点 metadata)
    fetch_node: Optional[Callable[[str], Optional[dict[str, Any]]]] = None
    # 规则需要调 LLM (summarize 用)
    llm_summarize: Optional[Callable[[list[ContextItem]], str]] = None


Rule = Callable[[list[ContextItem], RuleContext], None]
"""一个规则就是一个 (items, ctx) -> None 的函数, 就地改 items."""


def total_tokens(items: list[ContextItem]) -> int:
    """统计当前 items 总 token (跳过 summarized 因为它们不渲染)."""
    return sum(i.tokens for i in items if i.state != "summarized")


def apply_rules(
    rules: list[Rule],
    items: list[ContextItem],
    ctx: RuleContext,
) -> None:
    """按顺序跑 pipeline, 每个规则就地改 items.

    规则之间互不知道, 通过 items 的状态变化沟通。
    """
    for rule in rules:
        rule(items, ctx)
