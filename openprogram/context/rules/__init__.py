"""压缩规则 pipeline — 固定顺序的规则列表.

每个规则一个文件, 实现 _base.Rule 接口 ((items, ctx) -> None, 就地改).

顺序很重要, 改顺序等同于改规则版本:
  1. dedup        — 同 tool+args 老的标 aged (cheapest, runs first)
  2. tool_aging   — 老 tool result 替成语义 stub (基于 tail window)
  3. microcompact — 距上次 assistant 太久, 把老 result 清成固定占位符
  4. summarize    — 超 token 预算时 LLM 摘要 + 锚点保留 (最贵, 最后)

每个规则只动 locked=False 的 item, 已 locked 的跳过 (这套保证决策一次性,
新 snapshot 不会"重新审判"已经决定状态的 item).
"""
from __future__ import annotations

from ._base import Rule, RuleContext, apply_rules, total_tokens

# 各规则函数, 由 phase 1.3/1.4/1.5 实现填进来
from .aging import rule_tool_aging
from .microcompact import rule_microcompact
from .summarize import rule_summarize


# Pipeline 固定顺序。改顺序 / 加规则 → bump CURRENT_RULES_VERSION
RULE_PIPELINE: list[Rule] = [
    rule_tool_aging,
    rule_microcompact,
    rule_summarize,
]


__all__ = [
    "Rule",
    "RuleContext",
    "apply_rules",
    "total_tokens",
    "RULE_PIPELINE",
    "rule_tool_aging",
    "rule_microcompact",
    "rule_summarize",
]
