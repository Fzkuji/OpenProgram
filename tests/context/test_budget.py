from __future__ import annotations

from openprogram.context.budget import (
    BudgetAllocator,
    estimate_tools_breakdown,
)


class _FakeTool:
    """budget 只读 .schema/.spec、.name、_defer —— 无需真 AgentTool。"""
    def __init__(self, name, schema, defer=False):
        self.name = name
        self.schema = schema
        self._defer = defer


def _tool(name, defer=False):
    return _FakeTool(
        name,
        {"name": name, "description": "x" * 40, "parameters": {"type": "object"}},
        defer=defer,
    )


def test_breakdown_sum_equals_estimate_tools():
    """per-tool 之和必须等于旧的加总口径（自洽）。"""
    tools = [_tool("bash"), _tool("web_search", defer=True), _tool("read")]
    per = estimate_tools_breakdown(tools)
    assert sum(x["tokens"] for x in per) == BudgetAllocator._estimate_tools(tools)


def test_breakdown_marks_deferred_and_names():
    tools = [_tool("bash"), _tool("web_search", defer=True)]
    per = estimate_tools_breakdown(tools)
    by = {x["name"]: x for x in per}
    assert by["bash"]["deferred"] is False
    assert by["web_search"]["deferred"] is True
    assert by["bash"]["tokens"] > 0


def test_breakdown_empty():
    assert estimate_tools_breakdown([]) == []
