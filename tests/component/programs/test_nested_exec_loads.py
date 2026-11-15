"""A tool loaded inside a nested run stays inside it (deferred-tools L1).

A program called from a chat turn runs its own model loop (``runtime.exec``)
in the same task. What that loop loads with ``tool_search`` is read from its
own messages, so the outer chat's next request offers only what the outer
messages loaded, as Pi keeps loads in each run's own state.
"""
from __future__ import annotations

import asyncio

from openprogram.models.providers.types import (
    AgentTool, AgentToolResult, AssistantMessage, EventDone, EventStart, Model, TextContent,
    ToolCall, UserMessage,
)
from openprogram.programs._host.runtime import Tier, set_tier
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig

_MODEL = Model(id="fake", name="fake", api="completion", provider="fake",
               base_url="https://example.invalid")


def _tool(name: str, execute, *, deferred: bool = False) -> AgentTool:
    made = AgentTool(name=name, label=name, description=f"The {name} tool.",
                     parameters={"type": "object", "properties": {"select": {"type": "string"}}},
                     execute=execute)
    if deferred:
        set_tier(made, Tier.DEFERRED)
    return made


def _reply(content, stop="toolUse", at=1) -> AssistantMessage:
    return AssistantMessage(content=content, api="completion", provider="fake", model="fake",
                            stop_reason=stop, timestamp=at)


async def _search(call_id, args, cancel=None, on_update=None):
    name = str(args.get("select", "")).removeprefix("select:")
    return AgentToolResult(content=[TextContent(text=f"Loaded {name}.")], details={"loads": [{
        "name": name, "definition": {"name": name, "description": f"The {name} tool.",
                                     "parameters": {"type": "object", "properties": {}}}}]})


async def _cron(call_id, args, cancel=None, on_update=None):
    return AgentToolResult(content=[TextContent(text="scheduled")])


def test_a_tool_loaded_in_a_nested_run_is_not_offered_to_the_outer_chat():
    requests: dict[str, list[list[str]]] = {"outer": [], "inner": []}
    replies = {
        "outer": [_reply([ToolCall(id="o1", name="nested_program", arguments={})]),
                  _reply([TextContent(text="done")], "stop", 2)],
        "inner": [_reply([ToolCall(id="i1", name="tool_search", arguments={"select": "select:cron_probe"})]),
                  _reply([TextContent(text="inner done")], "stop", 2)],
    }

    def stream_fn(model, context, options):
        first = context.messages[0].content
        run = "inner" if first == "inner task" else "outer"
        requests[run].append([tool.name for tool in context.tools or []])
        message = replies[run].pop(0)

        async def events():
            yield EventStart(partial=message)
            yield EventDone(reason=message.stop_reason, message=message)

        return events()

    async def run(prompt: str, tools: list[AgentTool]):
        stream = agent_loop([UserMessage(content=prompt, timestamp=1)],
                            AgentContext(tools=tools, memory_prefetch=""),
                            AgentLoopConfig(model=_MODEL, convert_to_llm=lambda messages: messages),
                            stream_fn=stream_fn)
        async for _ in stream:
            pass
        return await stream.result()

    shared = [_tool("tool_search", _search), _tool("cron_probe", _cron, deferred=True)]

    async def nested(call_id, args, cancel=None, on_update=None):
        await run("inner task", list(shared))
        return AgentToolResult(content=[TextContent(text="program finished")])

    asyncio.run(run("outer task", [*shared, _tool("nested_program", nested)]))

    assert "cron_probe" not in requests["inner"][0]
    assert "cron_probe" in requests["inner"][1]
    assert len(requests["outer"]) == 2
    assert all("cron_probe" not in names for names in requests["outer"])
