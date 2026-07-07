"""端到端：defer 打标后，full toolset 的本地 tiktoken schema 确实缩小。

注意 tokenizer 口径：本地 o200k_base 数 full 全 schema ≈ 1650 token；provider
（如百炼/kimi）用自己的 tokenizer 数结构化 tool schema 会高得多（当年 ~14000 的
来源）。这里断言的是本地 breakdown 口径的缩小，与 provider 上报值是两个口径。
"""
from __future__ import annotations

from openprogram.functions import apply_default_deferral, agent_tools
from openprogram.context.breakdown import compute_call_breakdown

# 实测（2026-07-08，o200k_base）：defer 后常驻 24 工具 schema=672；上浮到 1000。
EXPECTED_MAX = 1000
EXPECTED_MIN_DEFERRED = 30   # 实测 deferred 35 个，下浮


def test_full_toolset_schema_shrunk_after_defer():
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    b = compute_call_breakdown(
        system_prompt="sys",
        history=[{"role": "user", "content": "hi"}],
        tools=tools,
        context_window=200_000,
    )
    assert b["tools_schema"] < EXPECTED_MAX, b["tools_schema"]
    deferred_n = sum(1 for t in b["tools"] if t["deferred"])
    assert deferred_n >= EXPECTED_MIN_DEFERRED, deferred_n
