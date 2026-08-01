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


class _ParametersTool:
    """The shape most registered tools actually have: the JSON schema
    lives on ``.parameters``, not ``.schema`` / ``.spec``."""

    def __init__(self, name, parameters):
        self.name = name
        self.parameters = parameters
        self._defer = False


def test_parameters_shaped_tools_are_priced_off_their_real_schema():
    """Regression: the fallback chain read only .schema/.spec, so every
    ``.parameters``-shaped tool fell through to the 20-token 'unknown
    tool' guess — pricing a ~6.8k-token toolset at ~690."""
    schema = {
        "type": "object",
        "properties": {
            f"field_{i}": {"type": "string", "description": "y" * 60}
            for i in range(20)
        },
    }
    tool = _ParametersTool("big_tool", schema)
    priced = estimate_tools_breakdown([tool])[0]["tokens"]

    assert priced > 20, "must not fall back to the unknown-tool guess"
    # Same number the equivalent .schema-shaped tool gets.
    assert priced == estimate_tools_breakdown(
        [_FakeTool("big_tool", schema)])[0]["tokens"]


def test_unknown_shaped_tool_still_falls_back():
    class _Opaque:
        name = "mystery"
        _defer = False

    assert estimate_tools_breakdown([_Opaque()])[0]["tokens"] == 20
