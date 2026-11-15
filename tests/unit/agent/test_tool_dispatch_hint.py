"""A deferred tool called before it was loaded (deferred-tools A6).

Valid arguments run, as Codex dispatches a deferred tool without a search.
A failed argument check of a tool whose schema the request did not send
says so and how to load it (Claude Code ``buildSchemaNotSentHint``). A
loaded tool whose source went away says why it is unavailable (A5).
"""
from __future__ import annotations

import asyncio
import time

from openprogram.models.providers.types import (
    AgentTool, AgentToolResult, AssistantMessage, TextContent, ToolCall, ToolResultMessage,
)
from openprogram.programs._host.runtime import Tier, mark_unavailable, schema_not_sent_hint, set_tier
from openprogram.runtime.agent.loop import _execute_tool_calls


class _Events:
    def __init__(self) -> None:
        self.pushed: list = []

    def push(self, event) -> None:
        self.pushed.append(event)


def _cron() -> AgentTool:
    async def execute(call_id, args, cancel, on_update):
        return AgentToolResult(content=[TextContent(text=f"scheduled {args['when']}")])

    tool = AgentTool(name="cron_hint_probe", label="cron", description="Schedule.",
                     parameters={"type": "object", "properties": {"when": {"type": "string"}},
                                 "required": ["when"]}, execute=execute)
    set_tier(tool, Tier.DEFERRED)
    return tool


def _loaded_history(name: str = "cron_hint_probe") -> list:
    return [ToolResultMessage(tool_call_id="s1", tool_name="tool_search", timestamp=0,
                              content=[TextContent(text="Loaded 1 deferred tool.")],
                              details={"loads": [{"name": name, "definition": {
                                  "name": name, "description": "Schedule.",
                                  "parameters": {"type": "object", "properties": {
                                      "when": {"type": "string"}}}}}]})]


def _call(tools, arguments, history, name="cron_hint_probe") -> ToolResultMessage:
    message = AssistantMessage(content=[ToolCall(id="c1", name=name, arguments=arguments)],
                               api="openai-completions", provider="openai", model="fake",
                               stop_reason="toolUse", timestamp=int(time.time() * 1000))
    result = asyncio.run(_execute_tool_calls(tools, message, None, _Events(), history=history))
    return result["tool_results"][0]


def test_bad_arguments_of_a_tool_not_loaded_say_the_schema_was_not_sent() -> None:
    result = _call([_cron()], {"at": "noon"}, history=[])
    assert result.is_error
    assert result.content[0].text.endswith(schema_not_sent_hint("cron_hint_probe"))
    assert "`tool_search select:cron_hint_probe`" in result.content[0].text


def test_good_arguments_of_a_tool_not_loaded_run() -> None:
    result = _call([_cron()], {"when": "noon"}, history=[])
    assert not result.is_error
    assert result.content[0].text == "scheduled noon"


def test_bad_arguments_of_a_loaded_tool_get_no_hint() -> None:
    result = _call([_cron()], {"at": "noon"}, history=_loaded_history())
    assert result.is_error
    assert "schema was not sent" not in result.content[0].text


def test_a_loaded_tool_that_is_gone_says_why() -> None:
    mark_unavailable(["gone_hint_probe"], "MCP server 'gone' is disconnected")
    result = _call([], {"when": "noon"}, history=_loaded_history("gone_hint_probe"), name="gone_hint_probe")
    assert result.is_error
    assert result.content[0].text == "Tool gone_hint_probe is unavailable: MCP server 'gone' is disconnected."
