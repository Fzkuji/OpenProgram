"""端到端：defer 打标后，full toolset 的本地 tiktoken schema 确实缩小。

注意 tokenizer 口径：本地 o200k_base 数 full 全 schema ≈ 1650 token；provider
（如百炼/kimi）用自己的 tokenizer 数结构化 tool schema 会高得多（当年 ~14000 的
来源）。这里断言的是本地 breakdown 口径的缩小，与 provider 上报值是两个口径。
"""
from __future__ import annotations

from openprogram.programs import (
    apply_default_deferral, agent_tools, install_loaded_deferred,
    release_turn_tools, split_tools_for_dispatch,
)
from openprogram.context.breakdown import compute_call_breakdown

# 实测（o200k_base）：defer 后常驻 21 工具 ≈4.6k token（描述+schema 都算），
# 全量 59 工具若都常驻 ≈13k。断言"缩小到不足全常驻的一半"而不是钉死绝对值，
# 这样加工具不会误伤，估算口径退化仍会被抓。
EXPECTED_MIN_DEFERRED = 22   # 实测 deferred 28 个，下浮


def test_full_toolset_schema_shrunk_after_defer():
    release_turn_tools()
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    b = compute_call_breakdown(
        system_prompt="sys",
        history=[{"role": "user", "content": "hi"}],
        tools=tools,
        context_window=200_000,
    )

    # 全部常驻时的价钱——defer 要真省下一大半才算生效。
    for t in tools:
        setattr(t, "_defer", False)
    try:
        all_resident = compute_call_breakdown(
            system_prompt="sys",
            history=[{"role": "user", "content": "hi"}],
            tools=tools,
            context_window=200_000,
        )["tools_schema"]
    finally:
        apply_default_deferral()

    assert b["tools_schema"] < all_resident / 2, (
        b["tools_schema"], all_resident)
    deferred_n = sum(1 for t in b["tools"] if t["deferred"])
    assert deferred_n >= EXPECTED_MIN_DEFERRED, deferred_n


def test_deferred_catalog_is_a_small_fraction_of_the_schema_cost():
    """目录只发裸名字，成本必须远小于常驻 schema——两者曾经反过来
    （目录估 4665、常驻估 588），错误互相抵消才显得总数合理。"""
    release_turn_tools()
    install_loaded_deferred()
    apply_default_deferral()
    tools = agent_tools(toolset="full")
    _, catalog = split_tools_for_dispatch(list(tools))
    b = compute_call_breakdown(
        system_prompt="sys",
        history=[{"role": "user", "content": "hi"}],
        tools=tools,
        context_window=200_000,
    )
    assert len(catalog) > len(tools) - len(catalog), "deferred 应占多数"
    assert b["tools_deferred_catalog"] < b["tools_schema"] / 4, (
        b["tools_deferred_catalog"], b["tools_schema"])
