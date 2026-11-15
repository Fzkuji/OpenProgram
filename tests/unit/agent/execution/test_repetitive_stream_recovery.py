from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.runtime.agent.loop import (
    RepetitiveOutputError,
    _cancel_response_stream,
    agent_loop,
)
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.models.providers.utils.errors import ExecInterrupt
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery


REPEATED_SEGMENT = (
    "I'll start by listing the current browser pages, then observe the existing "
    "weekly-report form if it's already open."
)


def _model() -> Model:
    return Model(
        id="stub",
        name="stub",
        api="completion",
        provider="fake",
        base_url="https://example.invalid",
    )


def _message(text: str = "", *, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion",
        provider="fake",
        model="stub",
        stop_reason=stop_reason,
        timestamp=int(time.time() * 1000),
    )


def _text_events(chunks: list[str], *, done: bool = True):
    async def events():
        partial = _message()
        yield EventStart(partial=partial)
        yield EventTextStart(content_index=0, partial=partial)
        text = ""
        for chunk in chunks:
            text += chunk
            partial = _message(text)
            yield EventTextDelta(content_index=0, delta=chunk, partial=partial)
        if done:
            yield EventTextEnd(content_index=0, content=text, partial=partial)
            yield EventDone(reason="stop", message=_message(text))

    return events()


def _tool() -> tuple[AgentTool, list[str]]:
    executed: list[str] = []

    async def execute(call_id, _args, _cancel_event, _on_update):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text="saved evidence")])

    return (
        AgentTool(
            name="read",
            label="read",
            description="read evidence",
            parameters={"type": "object"},
            execute=execute,
        ),
        executed,
    )


def _config(*, safe_point_hook=None) -> AgentLoopConfig:
    return AgentLoopConfig(
        model=_model(),
        convert_to_llm=lambda messages: messages,
        safe_point_hook=safe_point_hook,
    )


async def _collect(
    stream_fn, *, tools=None, cancel_event=None, safe_point_hook=None
):
    stream = agent_loop(
        [UserMessage(content="inspect", timestamp=1)],
        AgentContext(tools=tools, memory_prefetch=""),
        _config(safe_point_hook=safe_point_hook),
        cancel_event=cancel_event,
        stream_fn=stream_fn,
    )
    events = []
    async for event in stream:
        events.append(event)
    return await stream.result(), events


def _run(coro, state: RecoveryState):
    token = current_recovery.set(state)
    try:
        return asyncio.run(coro)
    finally:
        current_recovery.reset(token)


@pytest.mark.parametrize("width", [1, 3, 17])
def test_tool_enabled_repetition_recovers_current_response(width):
    tool, _executed = _tool()
    calls = []
    repeated_text = REPEATED_SEGMENT * 20
    chunks = [
        repeated_text[index:index + width]
        for index in range(0, len(repeated_text), width)
    ]

    def stream_fn(_model, context, _options):
        calls.append(context)
        if len(calls) == 1:
            return _text_events(chunks, done=False)
        return _text_events(["recovered"], done=True)

    state = RecoveryState(limit=2)
    messages, events = _run(_collect(stream_fn, tools=[tool]), state)

    assert [m.content[0].text for m in messages if m.role == "assistant"] == ["recovered"]
    assert len(calls) == 2
    assert state.used == 1
    assert state.requests == 2
    retry_events = [
        event.assistant_message_event
        for event in events
        if getattr(event, "type", None) == "message_update"
        and getattr(getattr(event, "assistant_message_event", None), "type", None)
        == "structured_output_retry"
    ]
    assert len(retry_events) == 1
    assert retry_events[0].issues[0]["code"] == "repetitive_output"


def test_no_tools_long_repeated_text_is_healthy():
    calls = []
    text = REPEATED_SEGMENT * 100

    def stream_fn(_model, context, _options):
        calls.append(context)
        return _text_events([REPEATED_SEGMENT] * 100, done=True)

    state = RecoveryState(limit=2)
    messages, _events = _run(_collect(stream_fn), state)

    assert messages[-1].content[0].text == text
    assert len(calls) == 1
    assert state.used == 0


def test_repetition_does_not_retry_after_cancel():
    tool, _executed = _tool()
    cancel_event = asyncio.Event()
    calls = []

    def stream_fn(_model, context, _options):
        calls.append(context)

        async def events():
            yield EventStart(partial=_message())
            cancel_event.set()
            raise ExecInterrupt("cancelled")

        return events()

    state = RecoveryState(limit=2)
    _messages, _events = _run(
        _collect(stream_fn, tools=[tool], cancel_event=cancel_event), state
    )

    assert len(calls) == 1
    assert state.used == 0
    assert state.phase == "cancelled"


def test_repetition_cancel_race_uses_exec_interrupt_boundary():
    tool, _executed = _tool()
    cancel_event = asyncio.Event()
    calls = []

    class CancelBeforeRecoveryStream:
        def __init__(self):
            self._events = _text_events([REPEATED_SEGMENT] * 20, done=False)

        def __aiter__(self):
            return self._events.__aiter__()

        async def cancel_producer(self):
            cancel_event.set()

    def stream_fn(_model, context, _options):
        calls.append(context)
        return CancelBeforeRecoveryStream()

    state = RecoveryState(limit=2)
    _messages, _events = _run(
        _collect(stream_fn, tools=[tool], cancel_event=cancel_event), state
    )

    assert len(calls) == 1
    assert state.used == 0
    assert state.phase == "cancelled"


def test_cancel_response_stream_preserves_current_task_cancellation():
    class CancelRaceStream:
        async def cancel_producer(self):
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await _cancel_response_stream(CancelRaceStream(), None)

    asyncio.run(exercise())


def test_cancel_response_stream_awaits_event_stream_producer_shutdown():
    async def exercise():
        stream = EventStream()
        producer_stopped = asyncio.Event()

        async def producer():
            try:
                await asyncio.Event().wait()
            finally:
                producer_stopped.set()

        task = asyncio.create_task(producer())
        stream.attach_producer(task)
        await asyncio.sleep(0)
        await _cancel_response_stream(stream, stream)

        assert task.done()
        assert producer_stopped.is_set()

    asyncio.run(exercise())


def test_repetition_exhaustion_is_paused():
    tool, _executed = _tool()
    calls = []

    def stream_fn(_model, context, _options):
        calls.append(context)
        return _text_events([REPEATED_SEGMENT] * 20, done=False)

    state = RecoveryState(limit=0)
    with pytest.raises(RepetitiveOutputError, match="repetitive_output") as raised:
        _run(_collect(stream_fn, tools=[tool]), state)

    assert len(calls) == 1
    assert state.used == 0
    assert state.phase == "paused"
    assert raised.value.transport_exhausted is True
    assert raised.value.retryable is False


def test_repetition_closes_provider_safe_point_before_retry():
    tool, _executed = _tool()
    calls = []
    safe_points = []

    async def safe_point(kind, payload):
        safe_points.append((kind, payload))
        return False

    def stream_fn(_model, context, _options):
        calls.append(context)
        if len(calls) == 1:
            return _text_events([REPEATED_SEGMENT] * 20, done=False)
        return _text_events(["recovered"], done=True)

    state = RecoveryState(limit=2)
    _messages, _events = _run(
        _collect(
            stream_fn,
            tools=[tool],
            safe_point_hook=safe_point,
        ),
        state,
    )

    kinds = [kind for kind, _payload in safe_points]
    assert kinds.count("provider.before") == 2
    assert kinds.count("provider.finished") == 1
    assert kinds.count("provider.after") == 1
    failed_payload = next(
        payload for kind, payload in safe_points if kind == "provider.finished"
    )
    assert failed_payload["message"]["error_message"] == "repetitive_output"


def test_completed_tool_round_is_kept_when_next_response_repeats():
    tool, executed = _tool()
    calls = []
    first = AssistantMessage(
        content=[ToolCall(id="call-1", name="read", arguments={})],
        api="completion",
        provider="fake",
        model="stub",
        stop_reason="toolUse",
        timestamp=1,
    )

    def stream_fn(_model, context, _options):
        calls.append(context)

        async def events():
            if len(calls) == 1:
                yield EventStart(partial=first)
                yield EventDone(reason="toolUse", message=first)
            elif len(calls) == 2:
                async for event in _text_events([REPEATED_SEGMENT] * 20, done=False):
                    yield event
            else:
                async for event in _text_events(["finished"], done=True):
                    yield event

        return events()

    state = RecoveryState(limit=2)
    messages, _events = _run(_collect(stream_fn, tools=[tool]), state)

    assert executed == ["call-1"]
    assert len(calls) == 3
    assert any(
        getattr(message, "role", None) == "toolResult"
        and message.tool_call_id == "call-1"
        for message in calls[2].messages
    )
    assert messages[-1].content[0].text == "finished"
