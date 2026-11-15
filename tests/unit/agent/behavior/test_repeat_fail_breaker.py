"""Same tool+args failing 3 times in one turn is not executed the 3rd time."""
from __future__ import annotations

import asyncio
import time

from openprogram.runtime.agent.loop import _execute_tool_calls
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import AssistantMessage, TextContent, ToolCall
from openprogram.models.providers.utils.event_stream import EventStream


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


def test_many_distinct_failures_keep_safe_point_payload_valid():
    from openprogram.runtime.execution.runtime_contract import MAX_AGENT_REPEAT_FAILURES

    async def execute(call_id, args, cancel, on_update):
        return AgentToolResult(content=[TextContent(text="failed")], details={}, is_error=True)

    tool = AgentTool(name="boom", description="fails", parameters={}, label="boom", execute=execute)
    failures = {}
    observed = []

    async def safe_point(kind, payload):
        if kind == "tool.after":
            observed.append(dict(payload["repeat_failures"]))
            assert len(payload["repeat_failures"]) <= MAX_AGENT_REPEAT_FAILURES
        return False

    async def run():
        for i in range(40):
            await _execute_tool_calls(
                [tool], _asst(str(i), "boom", {"x": i}), None, EventStream(),
                None, failures, safe_point_hook=safe_point,
            )
    asyncio.run(run())
    assert len(observed) == 40


def test_bash_description_changes_do_not_reset_repeat_detection():
    calls = []

    async def execute(call_id, args, cancel, on_update):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="denied")], details={}, is_error=True)

    tool = AgentTool(name="bash", description="shell", parameters={}, label="bash", execute=execute)
    failures = {}

    async def run():
        for i in range(3):
            await _execute_tool_calls(
                [tool], _asst(str(i), "bash", {"command": "git status", "description": str(i)}),
                None, EventStream(), None, failures,
            )
    asyncio.run(run())
    assert len(calls) == 2


def test_serial_tool_batch_has_no_parallel_group_and_reused_raw_id_is_qualified():
    async def execute(call_id, args, cancel, on_update):
        return AgentToolResult(content=[TextContent(text=args["value"])], details={})

    tool = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        label="echo",
        execute=execute,
    )
    assistant = AssistantMessage(
        content=[
            ToolCall(id="call_1", name="echo", arguments={"value": "a"}),
            ToolCall(id="call_2", name="echo", arguments={"value": "b"}),
        ],
        api="openai-completions", provider="openai", model="fake",
        stop_reason="toolUse", timestamp=int(time.time() * 1000),
    )
    ev = EventStream()

    async def run():
        await _execute_tool_calls(
            [tool], assistant, None, ev, round_id="round-1",
        )
        await _execute_tool_calls(
            [tool],
            _asst("call_1", "echo", {"value": "next"}),
            None,
            ev,
            round_id="round-2",
        )

    asyncio.run(run())
    starts = [item for item in list(ev._queue._queue) if getattr(item, "type", None) == "tool_execution_start"]
    assert len(starts) == 3
    assert all(item.group_id is None for item in starts)
    assert starts[0].occurrence_id != starts[2].occurrence_id
