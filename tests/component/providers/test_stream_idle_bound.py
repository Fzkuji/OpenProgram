"""Every streamed model request is read under an idle bound.

A scripted provider stalls before its first event, mid-stream and after a
tool call. With the bound shortened to a fraction of a second, each stall
ends as a retryable ``StreamIdleTimeout`` and the stalled provider stream is
closed; a slow but live stream and a provider that bounds itself are left
alone; a cancelled request stops at once; and a turn whose first reply
stalls is sent again and answers (the re-send itself is covered in
``test_stream_resend.py``).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import pytest

from openprogram.models.providers.catalog.api_registry import get_api_provider, register_api_provider
from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextStart,
    EventToolCallEnd,
    EventToolCallStart,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.models.providers.utils.errors import ErrorReason, classify_error
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout

IDLE_S = 0.3
_API = "idle-bound-test-api"


@pytest.fixture(autouse=True)
def _short_bound(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", str(IDLE_S))


def _model() -> Model:
    return Model(id="idle-model", name="Idle model", api=_API, provider="idle-provider",
                 base_url="http://idle.invalid")


def _context() -> Context:
    return Context(system_prompt="", messages=[UserMessage(role="user", content="hi", timestamp=0)])


def _message(model: Model, content=(), stop_reason="stop") -> AssistantMessage:
    return AssistantMessage(content=list(content), api=model.api, provider=model.provider,
                            model=model.id, stop_reason=stop_reason, timestamp=0)


class StallingProvider:
    """Yields its script; ``"stall"`` waits forever, ``("pause", s)`` waits ``s`` seconds."""

    def __init__(self, *scripts, bounds_own_stream_idle: bool = False) -> None:
        self.scripts = list(scripts)
        self.closed = 0
        self.calls = 0
        self.first_call_at: float | None = None
        self.bounds_own_stream_idle = bounds_own_stream_idle

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        script = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        if self.first_call_at is None:
            self.first_call_at = time.monotonic()
        try:
            for step in script:
                if step == "stall":
                    await asyncio.Event().wait()
                elif isinstance(step, tuple) and step[0] == "pause":
                    await asyncio.wait({asyncio.ensure_future(asyncio.Event().wait())}, timeout=step[1])
                elif step == "start":
                    yield EventStart(partial=_message(model))
                elif step == "text":
                    content = [TextContent(text="hello")]
                    yield EventTextStart(content_index=0, partial=_message(model, content))
                    yield EventTextDelta(content_index=0, delta="hello", partial=_message(model, content))
                elif step == "tool":
                    call = ToolCall(id="call_1", name="lookup", arguments={"q": "x"})
                    yield EventToolCallStart(content_index=0, partial=_message(model, [call]))
                    yield EventToolCallEnd(content_index=0, tool_call=call, partial=_message(model, [call]))
                elif step == "done":
                    content = [TextContent(text="answer")]
                    yield EventDone(reason="stop", message=_message(model, content))
        finally:
            self.closed += 1


async def _read(provider, options: SimpleStreamOptions | None = None):
    # Recovery off: the request is one attempt, so the bound's own outcome shows.
    from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery

    current_recovery.set(RecoveryState(limit=0))
    seen = []
    started = time.monotonic()
    try:
        async for event in stream_simple_with_provider(
            provider, _model(), _context(), options or SimpleStreamOptions(api_key="k"),
        ):
            seen.append(event.type)
    except Exception as exc:
        return seen, exc, time.monotonic() - started
    return seen, None, time.monotonic() - started


@pytest.mark.parametrize(
    "script, before_stall",
    [
        (["stall"], []),
        (["start", "text", "stall", "done"], ["start", "text_start", "text_delta"]),
        (["start", "tool", "stall", "done"], ["start", "toolcall_start", "toolcall_end"]),
    ],
    ids=["before-first-event", "mid-stream", "after-tool-call"],
)
def test_a_stalled_stream_ends_as_a_retryable_idle_timeout(script, before_stall):
    provider = StallingProvider(script)
    seen, error, took = asyncio.run(_read(provider))
    assert seen == before_stall
    assert isinstance(error, StreamIdleTimeout), error
    assert IDLE_S <= took < IDLE_S + 1.0, took
    assert provider.closed == 1  # the stalled provider stream was torn down
    assert classify_error(error) == (ErrorReason.TRANSPORT, True)


def test_a_slow_but_live_stream_is_not_cut():
    script = ["start"] + [("pause", IDLE_S * 0.6), "text"] * 4 + ["done"]
    seen, error, took = asyncio.run(_read(StallingProvider(script)))
    assert error is None
    assert seen[-1] == "done"
    assert took > IDLE_S * 2


def test_a_provider_with_its_own_bound_is_not_bounded_again():
    provider = StallingProvider(["start", ("pause", IDLE_S * 2), "done"], bounds_own_stream_idle=True)
    seen, error, _ = asyncio.run(_read(provider))
    assert error is None
    assert seen == ["start", "done"]


def test_a_cancelled_request_stops_at_once_during_a_stall():
    provider = StallingProvider(["start", "stall"])

    async def main():
        task = asyncio.ensure_future(_read(provider))
        await asyncio.wait({asyncio.ensure_future(asyncio.Event().wait())}, timeout=0.05)
        cancelled_at = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.monotonic() - cancelled_at

    stopped_in = asyncio.run(main())
    assert stopped_in < 0.1, stopped_in
    assert provider.closed == 1


@pytest.fixture
def registered() -> Iterator[StallingProvider]:
    previous = get_api_provider(_API)
    provider = StallingProvider(["start", "stall"], ["start", "text", "done"])
    register_api_provider(_API, provider)
    try:
        yield provider
    finally:
        from openprogram.models.providers.catalog.api_registry import unregister_api_provider

        if previous is None:
            unregister_api_provider(_API)
        else:
            register_api_provider(_API, previous)


def test_a_turn_whose_reply_stalls_is_retried_and_answers(registered, monkeypatch):
    from openprogram.models.providers.utils import stream_retry
    from openprogram.runtime.agent.loop.retry import RetrySettings
    from openprogram.runtime.agent.loop.session import AgentSession

    monkeypatch.setattr(stream_retry, "_BACKOFF_BASE_S", 0.01)
    session_retries = []
    resends = []

    class Bus:
        def emit(self, channel, event):
            if channel == "session" and event.get("type") == "auto_retry_start":
                session_retries.append(event)
            inner = getattr(event, "assistant_message_event", None)
            if channel == "agent" and getattr(inner, "type", None) == "retry":
                resends.append(inner)

    session = AgentSession(_model(), api_key="k", retry=RetrySettings(base_delay_ms=1), event_bus=Bus())
    final = asyncio.run(session.run("hi"))
    # From the first request, so a cold import of the agent loop is not timed.
    took = time.monotonic() - registered.first_call_at
    assert registered.calls == 2
    assert final is not None and final.stop_reason == "stop"
    assert [block.text for block in final.content] == ["answer"]
    assert IDLE_S <= took < IDLE_S + 2.0, took
    # The request layer sent it again; the session did not retry on top.
    assert session_retries == []
    assert len(resends) == 1 and "StreamIdleTimeout" in resends[0].error
    assert len([m for m in session.messages if m.role == "assistant"]) == 1
