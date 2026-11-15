from __future__ import annotations

import asyncio

import pytest

from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, TextContent, UserMessage,
)


def _message(text: str, reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion", provider="fake", model="fake",
        stop_reason=reason, timestamp=1,
    )


async def _run(responses: list[AssistantMessage]):
    seen = []

    def stream_fn(model, context, options):
        seen.append(list(context.messages))
        if not responses:
            raise AssertionError("unexpected provider request")
        message = responses.pop(0)

        async def events():
            yield EventStart(partial=_message(""))
            yield EventDone(reason=message.stop_reason, message=message)

        return events()

    config = AgentLoopConfig(
        model=Model(id="fake", name="fake", api="completion",
                    provider="fake", base_url="https://example.invalid"),
        convert_to_llm=lambda messages: messages,
    )
    stream = agent_loop([UserMessage(content="finish the task", timestamp=1)],
                        AgentContext(tools=[]), config, stream_fn=stream_fn)
    events = []
    async for event in stream:
        events.append(event)
    return await stream.result(), events, seen


def test_thinking_only_response_continues_to_a_visible_answer():
    messages, events, contexts = asyncio.run(_run([
        _message(""), _message("Done"),
    ]))
    assert len(contexts) == 2
    assert any(getattr(getattr(event, 'assistant_message_event', None), 'type', None) == 'retry'
               for event in events)
    assert any(isinstance(message, UserMessage) and "Continue" in str(message.content)
               for message in contexts[1])
    assert any(isinstance(message, AssistantMessage)
               and any(isinstance(block, TextContent) and block.text == "Done"
                       for block in message.content) for message in messages)


def test_length_response_continues_instead_of_ending_turn():
    _messages, _events, contexts = asyncio.run(_run([
        _message("partial", "length"), _message("complete"),
    ]))
    assert len(contexts) == 2


def test_length_response_drops_undispatched_streaming_tool_call():
    partial = _message("Checking the review", "length")
    partial.content.append({
        "type": "toolCall", "id": "call_incomplete|fc_incomplete",
        "name": "bash", "arguments": {}, "partial_json": '{"command":',
    })
    messages, _events, contexts = asyncio.run(_run([partial, _message("complete")]))
    assert len(contexts) == 2
    for history in (messages, contexts[1]):
        for message in history:
            if isinstance(message, AssistantMessage):
                assert all(isinstance(block, TextContent) for block in message.content)


def test_progressive_length_responses_do_not_hit_no_progress_limit():
    _messages, _events, contexts = asyncio.run(_run([
        _message("part one", "length"),
        _message("part two", "length"),
        _message("part three", "length"),
        _message("complete"),
    ]))
    assert len(contexts) == 4


def test_repeated_empty_responses_fail_clearly():
    with pytest.raises(RuntimeError, match="model_response_incomplete"):
        asyncio.run(_run([_message(""), _message(""), _message("")]))
