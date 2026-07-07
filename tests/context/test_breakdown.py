from __future__ import annotations

from openprogram.context.breakdown import compute_call_breakdown


class _FakeTool:
    def __init__(self, name, defer=False):
        self.name = name
        self.schema = {"name": name, "description": "d" * 30, "parameters": {}}
        self._defer = defer


def test_breakdown_shape_and_self_consistency():
    tools = [_FakeTool("bash"), _FakeTool("web_search", defer=True)]
    b = compute_call_breakdown(
        system_prompt="You are a helpful agent.",
        history=[{"role": "user", "content": "hello world"}],
        tools=tools,
        context_window=200_000,
    )
    # 分类和自洽：messages + system_prompt + tools_schema == input_used
    assert b["messages"] + b["system_prompt"] + b["tools_schema"] == b["input_used"]
    assert b["source"] == "local_tiktoken"
    names = {t["name"] for t in b["tools"]}
    assert names == {"bash", "web_search"}


def test_toolset_none_zero_tools():
    b = compute_call_breakdown(
        system_prompt="sys",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        context_window=200_000,
    )
    assert b["tools_schema"] == 0
    assert b["tools"] == []


def test_recompute_from_node_matches_direct():
    """从节点里存的工具名重算，应与直接用工具对象算的一致。"""
    from openprogram.context.breakdown import (
        compute_call_breakdown,
        compute_breakdown_from_node,
    )

    class _T:
        def __init__(self, n):
            self.name = n
            self.schema = {"name": n, "description": "d" * 20, "parameters": {}}
            self._defer = False

    tools = [_T("bash"), _T("read")]
    sys = "You are an agent."
    hist = [{"role": "user", "content": "do the thing"}]

    direct = compute_call_breakdown(
        system_prompt=sys, history=hist, tools=tools, context_window=128_000
    )
    node = {"metadata": {"tools_available": ["bash", "read"]}}
    recomputed = compute_breakdown_from_node(
        node, history=hist, system_prompt=sys, context_window=128_000,
        tool_resolver=lambda names: [_T(n) for n in names],
    )
    assert recomputed["tools_schema"] == direct["tools_schema"]
