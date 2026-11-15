"""The tools breakpoint goes on the last tool that is not deferred.

A ``defer_loading`` definition stays out of the cached prefix until it is
referenced and cannot carry ``cache_control``; a breakpoint on it would mark
nothing the provider caches.
"""
from __future__ import annotations

from openprogram.models.providers.settings.cache_policy import apply_cache_policy
from openprogram.models.providers.types import Context, Tool, UserMessage


class _DeferredTool(Tool):
    defer_loading: bool = True


def _tool(name: str, cls=Tool) -> Tool:
    return cls(name=name, description=name, parameters={"type": "object", "properties": {}})


def _marked(context: Context) -> list[str]:
    return [t.name for t in context.tools if t.cache_control is not None]


def _context(tools) -> Context:
    return Context(system_prompt="s", messages=[UserMessage(content="hi", timestamp=0)],
                   tools=tools)


def test_the_last_tool_carries_the_breakpoint():
    out = apply_cache_policy(_context([_tool("read"), _tool("write")]), "anthropic")
    assert _marked(out) == ["write"]


def test_a_deferred_tail_moves_the_breakpoint_to_the_last_resident_tool():
    tools = [_tool("read"), _tool("tool_search"),
             _tool("scheduler", _DeferredTool), _tool("cron", _DeferredTool)]
    out = apply_cache_policy(_context(tools), "anthropic")
    assert _marked(out) == ["tool_search"]


def test_only_deferred_tools_get_no_tools_breakpoint():
    out = apply_cache_policy(_context([_tool("scheduler", _DeferredTool)]), "anthropic")
    assert _marked(out) == []
