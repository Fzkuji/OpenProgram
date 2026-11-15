"""A steer after a response was voided for a re-send keeps nothing of it.

A request whose reply fails in transport is sent again and what it streamed
is void (a ``retry`` event with ``restart``). A steer that arrives during the
wait, before the new response says anything, ends the response: the message
it commits and the next request carry none of the voided text.
"""
from __future__ import annotations

import asyncio

from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventRetry,
    EventStart,
    EventTextDelta,
    EventTextStart,
    Model,
    TextContent,
    UserMessage,
)
from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig

VOID = "VOID PARTIAL ANSWER"
MODEL = Model(id="stub", name="stub", api="completion", provider="fake", base_url="https://example.invalid")


def _message(text: str = "") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)] if text else [], api=MODEL.api,
                            provider=MODEL.provider, model=MODEL.id, stop_reason="stop", timestamp=1)


def _texts(messages) -> list[str]:
    out = []
    for message in messages:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            out.append(content)
            continue
        for block in content or []:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                out.append(text)
    return out


def _run(*, restart: bool):
    waiting = asyncio.Event()
    requests: list[list[str]] = []

    async def first():
        yield EventStart(partial=_message())
        yield EventTextStart(content_index=0, partial=_message(VOID))
        yield EventTextDelta(content_index=0, delta=VOID, partial=_message(VOID))
        yield EventRetry(attempt=2, max_attempts=5, reason="transport", delay_ms=1000,
                         restart=True if restart else None)
        waiting.set()
        await asyncio.Event().wait()  # the backoff before the new attempt

    async def second():
        yield EventStart(partial=_message())
        yield EventDone(reason="stop", message=_message("after the steer"))

    def stream_fn(_model, context, _options):
        requests.append(_texts(context.messages))
        return first() if len(requests) == 1 else second()

    steered = False

    async def safe_point(kind, payload):
        nonlocal steered
        if kind == "provider.poll" and waiting.is_set() and not steered:
            steered = True
            payload["steer_pending"] = True
        return False

    pending: list[UserMessage] = []

    async def steering():
        if steered and not pending:
            pending.append(UserMessage(content="change of plan", timestamp=2))
            return list(pending)
        return []

    async def collect():
        stream = agent_loop(
            [UserMessage(content="inspect", timestamp=1)],
            AgentContext(memory_prefetch=""),
            AgentLoopConfig(model=MODEL, convert_to_llm=lambda messages: messages,
                            safe_point_hook=safe_point, get_steering_messages=steering),
            stream_fn=stream_fn,
        )
        async for _event in stream:
            pass
        return await stream.result()

    messages = asyncio.run(asyncio.wait_for(collect(), timeout=20))
    return requests, _texts(messages)


def test_a_steer_during_the_wait_commits_nothing_of_the_voided_response():
    requests, stored = _run(restart=True)

    assert len(requests) == 2
    assert VOID not in " ".join(requests[1])
    assert VOID not in " ".join(stored)
    assert "after the steer" in stored


def test_a_steer_after_a_retry_that_voids_nothing_keeps_the_partial():
    requests, stored = _run(restart=False)

    assert len(requests) == 2
    assert VOID in " ".join(requests[1])
    assert VOID in " ".join(stored)


def test_a_steer_keeps_only_the_text_that_was_streamed():
    """A provider adds a delta to its partial before the loop reads the
    delta's event; a steer in between keeps, and sends back, what went out."""
    ahead = asyncio.Event()
    requests: list[list[str]] = []
    streamed: list[str] = []

    async def first():
        partial = _message()
        yield EventStart(partial=partial)
        partial.content.append(TextContent(text=""))
        yield EventTextStart(content_index=0, partial=partial)
        partial.content[0].text += "Once upon"
        yield EventTextDelta(content_index=0, delta="Once upon", partial=partial)
        partial.content[0].text += " a time"
        ahead.set()
        await asyncio.Event().wait()

    async def second():
        yield EventStart(partial=_message())
        yield EventDone(reason="stop", message=_message("after the steer"))

    def stream_fn(_model, context, _options):
        requests.append(_texts(context.messages))
        return first() if len(requests) == 1 else second()

    steered = False

    async def safe_point(kind, payload):
        nonlocal steered
        if kind == "provider.poll" and ahead.is_set() and not steered:
            steered = True
            payload["steer_pending"] = True
        return False

    pending: list[UserMessage] = []

    async def steering():
        if steered and not pending:
            pending.append(UserMessage(content="change of plan", timestamp=2))
            return list(pending)
        return []

    async def collect():
        stream = agent_loop(
            [UserMessage(content="inspect", timestamp=1)],
            AgentContext(memory_prefetch=""),
            AgentLoopConfig(model=MODEL, convert_to_llm=lambda messages: messages,
                            safe_point_hook=safe_point, get_steering_messages=steering),
            stream_fn=stream_fn,
        )
        async for event in stream:
            inner = getattr(event, "assistant_message_event", None)
            if getattr(inner, "type", None) == "text_delta":
                streamed.append(inner.delta)
        return await stream.result()

    messages = asyncio.run(asyncio.wait_for(collect(), timeout=20))

    assert streamed == ["Once upon"]
    assert requests[1] == ["inspect", "Once upon", "change of plan"]
    assert _texts(messages) == ["inspect", "Once upon", "change of plan", "after the steer"]
