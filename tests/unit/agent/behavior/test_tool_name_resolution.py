"""Observed Claude-style tool spellings resolve only inside the active tool list."""
import asyncio
import time

import pytest

from openprogram.runtime.agent.loop import _execute_tool_calls, agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import AssistantMessage, EventDone, EventStart, Model, TextContent, ToolCall, UserMessage
from openprogram.models.providers.utils.event_stream import EventStream


def _call(name, args, tools):
    message = AssistantMessage(
        content=[ToolCall(id="call-1", name=name, arguments=args)],
        api="anthropic-messages", provider="anthropic", model="test",
        stop_reason="toolUse", timestamp=int(time.time() * 1000),
    )
    result = asyncio.run(_execute_tool_calls(tools, message, None, EventStream()))
    return result["tool_results"][0]


def _tool(name, fields, seen):
    async def execute(call_id, args, cancel, on_update):
        seen.append(args)
        return AgentToolResult(content=[TextContent(text="ok")], details={})

    return AgentTool(
        name=name, label=name, description=name,
        parameters={"type": "object", "properties": fields,
                    "required": list(fields), "additionalProperties": False},
        execute=execute,
    )


@pytest.mark.parametrize("called,available,args", [
    ("Bash", "bash", {"command": "pwd"}),
    ("Read", "read", {"file_path": "/tmp/example"}),
    ("ToolSearch", "tool_search", {"query": "select:report"}),
])
def test_claude_spelling_uses_available_tool(called, available, args):
    seen = []
    field = "select" if available == "tool_search" else next(iter(args))
    result = _call(called, args, [_tool(available, {field: {"type": "string"}}, seen)])
    assert not result.is_error
    assert seen == [{field: next(iter(args.values()))}]
    assert result.tool_name == called


def test_alias_cannot_invoke_absent_tool():
    result = _call("Bash", {"command": "pwd"}, [])
    assert result.is_error
    assert "not found" in result.content[0].text


def test_invalid_alias_arguments_are_rejected():
    seen = []
    result = _call("Bash", {}, [_tool("bash", {"command": {"type": "string"}}, seen)])
    assert result.is_error
    assert "Validation failed" in result.content[0].text
    assert seen == []


def test_tool_search_omits_null_optional_limit():
    seen = []
    tool = _tool("tool_search", {"select": {"type": "string"}}, seen)
    result = _call("tool_search", {"select": "select:report", "max_results": None}, [tool])
    assert not result.is_error
    assert seen == [{"select": "select:report"}]


def test_exact_registered_name_takes_precedence():
    upper, lower = [], []
    tools = [_tool("Bash", {"command": {"type": "string"}}, upper),
             _tool("bash", {"command": {"type": "string"}}, lower)]
    result = _call("Bash", {"command": "pwd"}, tools)
    assert not result.is_error
    assert upper == [{"command": "pwd"}]
    assert lower == []


def test_public_agent_loop_executes_observed_alias():
    seen = []
    tool = _tool("read", {"file_path": {"type": "string"}}, seen)
    replies = [
        AssistantMessage(
            content=[ToolCall(id="call-1", name="Read", arguments={"file_path": "/tmp/example"})],
            api="completion", provider="fake", model="fake", stop_reason="toolUse", timestamp=1,
        ),
        AssistantMessage(
            content=[TextContent(text="done")], api="completion", provider="fake",
            model="fake", stop_reason="stop", timestamp=2,
        ),
    ]

    def stream_fn(model, context, options):
        message = replies.pop(0)

        async def events():
            yield EventStart(partial=message)
            yield EventDone(reason=message.stop_reason, message=message)

        return events()

    async def run():
        stream = agent_loop(
            [UserMessage(content="read file", timestamp=1)],
            AgentContext(tools=[tool], memory_prefetch=""),
            AgentLoopConfig(
                model=Model(id="fake", name="fake", api="completion", provider="fake",
                            base_url="https://example.invalid"),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        async for _ in stream:
            pass
        return await stream.result()

    result = asyncio.run(run())
    assert seen == [{"file_path": "/tmp/example"}]
    assert any(getattr(message, "tool_name", None) == "Read" and not message.is_error
               for message in result)
