"""Tool calls a model wrote into its text become real calls.

Each template shape (Hermes JSON, GLM pairs, Claude/MiniMax invoke,
DeepSeek DSML) is read when every block names an offered tool; anything
less leaves the reply as the text it was.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    EventDone,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.text_tool_calls import (
    read_written_calls,
    recover_text_tool_calls,
)
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig

OFFERED = {"bash", "edit", "ToolSearch"}


@pytest.mark.parametrize("text, before, calls", [
    (
        'Checking.\n<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>',
        "Checking.", [("bash", {"command": "ls"})],
    ),
    (
        '<tool_call>{"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}</tool_call>',
        "", [("bash", {"command": "ls"})],
    ),
    (
        "<tool_call>edit<arg_key>path</arg_key><arg_value>a.md</arg_value>"
        "<arg_key>count</arg_key><arg_value>2</arg_value></tool_call>",
        "", [("edit", {"path": "a.md", "count": 2})],
    ),
    (
        '<function_calls>\n<invoke name="ToolSearch">\n<parameter name="query">browser</parameter>\n'
        "</invoke>\n</function_calls>",
        "", [("ToolSearch", {"query": "browser"})],
    ),
    (
        'Running it.\n\n<｜DSML｜function_calls>\n<｜DSML｜invoke name="bash">\n'
        '<｜DSML｜parameter name="command" string="true">ls -la</｜DSML｜parameter>\n'
        '<｜DSML｜parameter name="timeout" string="false">30</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n</｜DSML｜function_calls>",
        "Running it.", [("bash", {"command": "ls -la", "timeout": 30})],
    ),
    (
        '看一下。<tool_call>\n<||DSML||invoke name="bash">\n<|DSML|parameter name="command" '
        'string="true">ls</|DSML|parameter>\n</ ||DSML|| invoke>\n</tool_call>',
        "看一下。", [("bash", {"command": "ls"})],
    ),
    (
        '<invoke name="bash"><parameter name="command">ls</parameter></invoke>\n'
        '<invoke name="edit"><parameter name="path">a.md</parameter></invoke>',
        "", [("bash", {"command": "ls"}), ("edit", {"path": "a.md"})],
    ),
    (
        '<invoke name="ToolSearch"><parameter name="query">x</parameter></invoke>\n'
        "*Result: Tool loaded.*",
        "", [("ToolSearch", {"query": "x"})],
    ),
])
def test_each_written_shape_is_read(text, before, calls):
    written = read_written_calls(text, OFFERED)
    assert written is not None
    assert written.before == before
    assert written.calls == calls


@pytest.mark.parametrize("text", [
    "No calls here, just an answer.",
    '<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>',  # not offered
    '<tool_call>{"name": "bash", "arguments": {"command": </tool_call>',  # unreadable
    "Use `<tool_call>` tags when you call tools.",
    '<invoke name="bash">oops not a parameter</invoke>',
])
def test_anything_less_is_left_as_text(text):
    assert read_written_calls(text, OFFERED) is None


def _reply(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)], api="completion",
                            provider="fake", model="stub", stop_reason=stop_reason, timestamp=1)


class _Named:
    def __init__(self, name):
        self.name = name


def test_a_reply_is_rewritten_in_place_only_when_tools_were_offered():
    text = '<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>'
    unoffered = _reply(text)
    assert not recover_text_tool_calls(unoffered, [])
    assert unoffered.stop_reason == "stop"

    reply = _reply(text)
    assert recover_text_tool_calls(reply, [_Named("bash")])
    assert reply.stop_reason == "toolUse"
    [call] = reply.content
    assert isinstance(call, ToolCall) and call.name == "bash" and call.arguments == {"command": "ls"}


def test_a_reply_that_made_a_real_call_is_left_alone():
    reply = _reply('<tool_call>{"name": "bash", "arguments": {}}</tool_call>', stop_reason="toolUse")
    reply.content.append(ToolCall(id="c1", name="edit", arguments={}))
    assert not recover_text_tool_calls(reply, [_Named("bash"), _Named("edit")])


def test_the_agent_loop_runs_a_written_call():
    ran: list[dict] = []

    async def execute(_call_id, args, _cancel, _on_update):
        ran.append(args)
        return AgentToolResult(content=[TextContent(text="file.txt")])

    tool = AgentTool(name="bash", label="bash", description="shell",
                     parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                     execute=execute)
    replies = [
        _reply('Listing.\n<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>'),
        _reply("There is file.txt."),
    ]
    seen = []

    def stream_fn(_model, context, _options):
        seen.append(list(context.messages))
        reply = replies[len(seen) - 1]

        async def events():
            yield EventStart(partial=reply)
            yield EventDone(reason="stop", message=reply)

        return events()

    async def run():
        stream = agent_loop(
            [UserMessage(content="what is here", timestamp=1)],
            AgentContext(tools=[tool], memory_prefetch=""),
            AgentLoopConfig(
                model=Model(id="stub", name="stub", api="completion", provider="fake",
                            base_url="https://example.invalid"),
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream_fn,
        )
        async for _ in stream:
            pass
        return await stream.result()

    messages = asyncio.run(asyncio.wait_for(run(), 10))
    assert ran == [{"command": "ls"}]
    first = messages[1]
    assert first.stop_reason == "toolUse"
    assert [type(block).__name__ for block in first.content] == ["TextContent", "ToolCall"]
    assert messages[-1].content[0].text == "There is file.txt."
