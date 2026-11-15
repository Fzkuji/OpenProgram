"""Continuation ordered-card seeding uses full turn_display, not only the last decision."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from tests.support.agent_journal import journal_continuation
from openprogram.runtime.conversation.checkpoint import AgentContinuation, runtime_contract_snapshot
from openprogram.runtime.conversation.turn_runtime.loop_runner import (
    _ordered_block_from_content,
    _seed_continuation_cards,
)
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.state.reply_text import ReplyText, reply_text
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import (
    AssistantMessage,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
)


def _model():
    return Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )


async def _fixture_execute(_call_id, _args, _cancel, _update):
    return AgentToolResult(content=[TextContent(text="fixture")])


def _page_tool():
    return AgentTool(
        name="page_tool", description="page_tool",
        parameters={"type": "object"}, label="page_tool",
        execute=_fixture_execute,
    )


def _request():
    request = TurnRequest(
        session_id="session", user_text="run", agent_id="main", source="test",
        user_msg_id="user-1", user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    return request


def _snapshot(request, tools=None):
    return runtime_contract_snapshot(
        model=_model(), system_prompt="",
        tools=list(tools if tools is not None else [_page_tool()]),
        request=request,
    )


def _display_cards():
    return [
        {"type": "text", "text": "Counter is 1"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-1",
            "input": '{"command": "observe"}', "result": "Counter: 1", "is_error": False,
        },
        {"type": "text", "text": "Counter is 2"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-2",
            "input": '{"command": "act"}', "result": "Counter: 2", "is_error": False,
        },
        {"type": "text", "text": "Counter is 3"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-3",
            "input": '{"command": "observe"}', "result": "Counter: 3", "is_error": False,
        },
    ]


def test_ordered_block_from_content_maps_text_thinking_and_tool():
    assert _ordered_block_from_content(TextContent(text="hello")) == {
        "type": "text", "text": "hello",
    }
    thinking = SimpleNamespace(type="thinking", thinking="plan")
    assert _ordered_block_from_content(thinking) == {
        "type": "thinking", "text": "plan",
    }
    tool = ToolCall(id="call-pre", name="page_tool", arguments={"command": "observe"})
    mapped = _ordered_block_from_content(tool)
    assert mapped["type"] == "tool"
    assert mapped["tool_call_id"] == "call-pre"


def test_seed_uses_full_display_when_current_decision_is_only_the_last_round():
    last = AssistantMessage(
        content=[
            TextContent(text="Counter is 3"),
            ToolCall(id="call-3", name="page_tool", arguments={"command": "observe"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    continuation = SimpleNamespace(
        display=tuple(_display_cards()),
        assistant_message=last,
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-3", tool_name="page_tool",
                content=[TextContent(text="Counter: 3")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    tool_calls: list[dict] = []
    text_parts = ReplyText()
    seen_tools = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=tool_calls,
        reply=text_parts,
    )
    assert [block.get("tool_call_id") or block.get("text") for block in ordered] == [
        "Counter is 1", "call-1", "Counter is 2", "call-2", "Counter is 3", "call-3",
    ]
    assert ordered[1]["result"] == "Counter: 1"
    assert ordered[1]["is_error"] is False
    # The reply paused is one reply: its flat text is every passage it
    # showed, as the row the stream tap kept while it ran reads.
    assert text_parts.text == "Counter is 1\n\nCounter is 2\n\nCounter is 3" == reply_text(ordered)
    # The passage the resumed response writes follows the tool call it waited on.
    assert text_parts.add({"type": "text", "text": "Done."}) == "\n\nDone."
    assert seen_tools == {"call-1", "call-2", "call-3"}
    assert {row["tool_call_id"] for row in tool_calls} == {"call-1", "call-2", "call-3"}


def test_seed_falls_back_to_current_decision_when_display_is_absent():
    continuation = SimpleNamespace(
        display=(),
        assistant_message=SimpleNamespace(content=[
            TextContent(text="Counter is 4"),
            ToolCall(id="call-pre", name="page_tool", arguments={"command": "observe"}),
        ]),
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-pre", tool_name="page_tool",
                content=[TextContent(text="Counter: 4")], timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call-orphan", tool_name="page_tool",
                content=[TextContent(text="listed")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    tool_calls: list[dict] = []
    text_parts = ReplyText()
    seen = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=tool_calls,
        reply=text_parts,
    )
    assert text_parts.text == "Counter is 4"
    assert [block["type"] for block in ordered] == ["text", "tool", "tool"]
    assert ordered[1]["tool_call_id"] == "call-pre"
    assert ordered[2]["tool_call_id"] == "call-orphan"
    assert seen == {"call-pre", "call-orphan"}


def test_seed_merges_current_decision_when_display_is_a_prefix():
    last = AssistantMessage(
        content=[
            TextContent(text="saved-now"),
            ToolCall(id="call-3", name="page_tool", arguments={"command": "observe"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    prefix = [
        {"type": "text", "text": "repeated"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-1",
            "input": "{}", "result": "ok", "is_error": False,
        },
    ]
    continuation = SimpleNamespace(
        display=tuple(prefix),
        assistant_message=last,
        tool_results=(
            ToolResultMessage(
                tool_call_id="call-3", tool_name="page_tool",
                content=[TextContent(text="Counter: 3")], timestamp=1,
            ),
        ),
    )
    ordered: list[dict] = []
    seen = _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        reply=ReplyText(),
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["repeated", "call-1", "saved-now", "call-3"]
    assert seen == {"call-1", "call-3"}


def test_seed_appends_only_unmatched_tail_when_display_ends_mid_decision():
    last = AssistantMessage(
        content=[
            TextContent(text="Current"),
            ToolCall(id="call-new", name="page_tool", arguments={"command": "act"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    display = [
        {"type": "text", "text": "Old"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-old",
            "result": "ok", "is_error": False,
        },
        {"type": "text", "text": "Current"},
    ]
    continuation = SimpleNamespace(
        display=tuple(display),
        assistant_message=last,
        tool_results=(),
    )
    ordered: list[dict] = []
    _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        reply=ReplyText(),
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["Old", "call-old", "Current", "call-new"]
    assert labels.count("Current") == 1


def test_seed_appends_remaining_tools_when_display_ends_on_first_tool_of_decision():
    last = AssistantMessage(
        content=[
            TextContent(text="Current"),
            ToolCall(id="call-a", name="page_tool", arguments={"command": "observe"}),
            ToolCall(id="call-b", name="page_tool", arguments={"command": "act"}),
        ],
        api="fake", provider="fake", model="fake", timestamp=1, stop_reason="toolUse",
    )
    display = [
        {"type": "text", "text": "Old"},
        {"type": "text", "text": "Current"},
        {
            "type": "tool", "tool": "page_tool", "tool_call_id": "call-a",
            "result": "ok", "is_error": False,
        },
    ]
    continuation = SimpleNamespace(
        display=tuple(display),
        assistant_message=last,
        tool_results=(),
    )
    ordered: list[dict] = []
    _seed_continuation_cards(
        continuation,
        ordered_blocks_out=ordered,
        tool_calls=[],
        reply=ReplyText(),
    )
    labels = [block.get("tool_call_id") or block.get("text") for block in ordered]
    assert labels == ["Old", "Current", "call-a", "call-b"]
    assert ordered[2]["result"] == "ok"


def test_from_checkpoint_rebuilds_display_from_the_journal():
    request = _request()
    last = AssistantMessage(
        content=[
            TextContent(text="Counter is 3"),
            ToolCall(id="call-3", name="page_tool", arguments={"command": "observe"}),
        ],
        api="openai-completions", provider="openai", model="fake",
        timestamp=1, stop_reason="toolUse",
    )
    last_result = ToolResultMessage(
        tool_call_id="call-3", tool_name="page_tool",
        content=[TextContent(text="Counter: 3")], timestamp=1,
    )
    earlier = AssistantMessage(
        content=[ToolCall(id="call-1", name="page_tool", arguments={"command": "observe"})],
        api="openai-completions", provider="openai", model="fake",
        timestamp=1, stop_reason="toolUse",
    )
    earlier_result = ToolResultMessage(
        tool_call_id="call-1", tool_name="page_tool",
        content=[TextContent(text="Counter: 1")], timestamp=1,
    )
    continuation = journal_continuation(
        request, decision=last, snapshot=_snapshot(request), tool_results=(last_result,),
        earlier=((earlier, (earlier_result,)),),
    )
    tools = [card for card in continuation.display if card.get("type") == "tool"]
    assert [card["tool_call_id"] for card in tools] == ["call-1", "call-3"]
    assert tools[0]["result"] == "Counter: 1"
    assert continuation.assistant_message.content[0].text == "Counter is 3"
