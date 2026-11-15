"""After a long stretch of tool rounds with nothing shown, a watched turn is asked for a word of progress."""
from __future__ import annotations

import asyncio

from openprogram.models.providers.types import (
    AgentTool, AgentToolResult, AssistantMessage, EventDone, EventStart, Model, TextContent, ThinkingContent,
    ToolCall, UserMessage,
)
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.progress_note import NOTE, QUIET_ROUNDS, QuietStretch
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig


def _reply(*blocks, reason: str = "toolUse") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api="completion", provider="fake", model="fake",
                            stop_reason=reason, timestamp=1)


def _call(number: int) -> ToolCall:
    return ToolCall(id=f"call-{number}", name="step", arguments={"n": number})


def test_the_note_is_due_after_the_quiet_rounds_and_counts_again():
    quiet = QuietStretch(rounds=3)
    assert [quiet.observe(_reply(_call(n))) for n in range(3)] == [None, None, NOTE]
    assert quiet.observe(_reply(_call(4))) is None


def test_text_or_shown_thinking_starts_the_count_again():
    quiet = QuietStretch(rounds=2)
    assert quiet.observe(_reply(_call(1))) is None
    assert quiet.observe(_reply(TextContent(text="Reading the logs."), _call(2))) is None
    assert quiet.observe(_reply(_call(3))) is None
    assert quiet.observe(_reply(ThinkingContent(thinking="compare the two"), _call(4))) is None
    assert quiet.observe(_reply(ThinkingContent(thinking="  "), _call(5))) is None
    assert quiet.observe(_reply(_call(6))) == NOTE


def _run(rounds: int, *, watched: bool):
    seen: list[list] = []
    replies = [_reply(_call(n)) for n in range(rounds)] + [_reply(TextContent(text="Done"), reason="stop")]

    async def execute(_call_id, _args, _cancel, _update):
        return AgentToolResult(content=[TextContent(text="ok")])

    def stream_fn(_model, context, _options):
        seen.append(list(context.messages))
        message = replies.pop(0)

        async def events():
            yield EventStart(partial=_reply())
            yield EventDone(reason=message.stop_reason, message=message)

        return events()

    config = AgentLoopConfig(
        model=Model(id="fake", name="fake", api="completion", provider="fake", base_url="https://example.invalid"),
        convert_to_llm=lambda messages: messages, progress_notes=watched,
    )
    tool = AgentTool(name="step", description="step", parameters={"type": "object"}, label="step", execute=execute)

    async def drive():
        stream = agent_loop([UserMessage(content="do it", timestamp=1)], AgentContext(tools=[tool]), config,
                            stream_fn=stream_fn)
        async for _event in stream:
            pass
        return await stream.result()

    return asyncio.run(drive()), seen


def _notes(messages) -> int:
    return sum(isinstance(message, UserMessage) and message.content == NOTE for message in messages)


def test_a_watched_turn_gets_one_note_after_the_quiet_rounds_and_it_is_not_kept():
    kept, seen = _run(QUIET_ROUNDS + 1, watched=True)
    # Not in the request of the round that made it due, in every request after it.
    assert [_notes(context) for context in seen] == [0] * QUIET_ROUNDS + [1, 1]
    assert _notes(kept) == 0


def test_a_turn_nobody_watches_gets_none():
    _kept, seen = _run(QUIET_ROUNDS + 1, watched=False)
    assert all(_notes(context) == 0 for context in seen)
