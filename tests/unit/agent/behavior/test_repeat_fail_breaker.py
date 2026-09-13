"""Same tool+args failing 3 times in one turn is not executed the 3rd time."""
from __future__ import annotations

import asyncio
import time

from openprogram.agent.agent_loop import _execute_tool_calls
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import AssistantMessage, TextContent, ToolCall
from openprogram.providers.utils.event_stream import EventStream


def _asst(call_id: str, name: str, args: dict) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments=args)],
        api="openai-completions",
        provider="openai",
        model="fake",
        stop_reason="toolUse",
        timestamp=int(time.time() * 1000),
    )


async def _run_once(tools, args, repeat_failures, *, call_id="c"):
    ev = EventStream()
    out = await _execute_tool_calls(
        tools, _asst(call_id, "boom", args), None, ev, None, repeat_failures,
    )
    return out["tool_results"][0]


def test_third_identical_failure_is_not_executed():
    hits = {"n": 0}

    async def execute(call_id, args, cancel, on_update):
        hits["n"] += 1
        return AgentToolResult(
            content=[TextContent(text="nope")],
            details={},
            is_error=True,
        )

    tool = AgentTool(
        name="boom",
        description="always fails",
        parameters={"type": "object", "properties": {}},
        label="boom",
        execute=execute,
    )
    failures: dict[str, int] = {}
    args = {"command": "act", "arguments": {}}

    first = asyncio.run(_run_once([tool], args, failures, call_id="1"))
    second = asyncio.run(_run_once([tool], args, failures, call_id="2"))
    third = asyncio.run(_run_once([tool], args, failures, call_id="3"))

    assert hits["n"] == 2
    assert first.is_error and first.content[0].text == "nope"
    assert second.is_error and second.content[0].text == "nope"
    assert third.is_error
    assert "连续 3 次" in third.content[0].text
    assert "改变方法" in third.content[0].text


def test_changed_args_are_not_tripped():
    hits = {"n": 0}

    async def execute(call_id, args, cancel, on_update):
        hits["n"] += 1
        return AgentToolResult(
            content=[TextContent(text="nope")],
            details={},
            is_error=True,
        )

    tool = AgentTool(
        name="boom",
        description="always fails",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        label="boom",
        execute=execute,
    )
    failures: dict[str, int] = {}
    asyncio.run(_run_once([tool], {"x": "a"}, failures, call_id="1"))
    asyncio.run(_run_once([tool], {"x": "a"}, failures, call_id="2"))
    third = asyncio.run(_run_once([tool], {"x": "b"}, failures, call_id="3"))
    assert hits["n"] == 3
    assert third.content[0].text == "nope"
