from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextStart,
    EventThinkingStart,
    TextContent,
    Usage,
)
from openprogram.models.providers.utils.event_stream import AssistantMessageEventStream
from openprogram.models.providers.utils.failover import stream_with_failover
from openprogram.models.providers.utils.failover import FailoverCategory, failover_category
from openprogram.models.providers.utils.stream_retry import ProviderStreamError


class _Model:
    def __init__(self, mid: str) -> None:
        self.id = mid
        self.provider = "test"


def test_retryable_provider_eof_is_failover_worthy() -> None:
    error = ProviderStreamError(
        "Codex SSE ended before a terminal response event",
        retryable=True,
    )
    assert failover_category(error) is FailoverCategory.NETWORK


def test_quota_exhausted_is_not_failover_worthy() -> None:
    error = ProviderStreamError(
        'HTTP 429: {"error":{"type":"usage_limit_reached","plan_type":"pro"}}',
        http_status=429,
        error_text='{"error":{"type":"usage_limit_reached","plan_type":"pro"}}',
        retryable=True,
    )
    assert failover_category(error) is FailoverCategory.NONE


def test_nonretryable_provider_eof_is_not_failover_worthy() -> None:
    error = ProviderStreamError(
        "Codex SSE ended before a terminal response event",
        retryable=False,
    )
    assert failover_category(error) is FailoverCategory.NONE


def _message(model: _Model) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api="openai-responses",
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        timestamp=1,
    )


def test_empty_thinking_placeholder_still_allows_model_failover() -> None:
    async def run() -> None:
        primary = _Model("primary")
        fallback = _Model("fallback")
        called: list[str] = []

        def base_stream(model, _context, _options):
            called.append(model.id)
            stream = AssistantMessageEventStream()
            message = _message(model)

            async def produce() -> None:
                stream.push(EventStart(partial=message))
                if model is primary:
                    stream.push(EventThinkingStart(content_index=0, partial=message))
                    stream.fail(ProviderStreamError("early EOF", retryable=True))
                    return
                stream.push(EventTextStart(content_index=0, partial=message))
                message.content = [TextContent(text="ok")]
                stream.push(EventTextDelta(content_index=0, delta="ok", partial=message))
                stream.push(EventDone(reason="stop", message=message))

            asyncio.create_task(produce())
            return stream

        stream = stream_with_failover(base_stream, primary, object(), None, [fallback])
        events = [event async for event in stream]

        assert called == ["primary", "fallback"]
        assert [event.type for event in events].count("start") == 1
        assert events[-1].type == "done"
        assert events[-1].message.model == "fallback"
        assert events[-1].message.failover == [
            {"provider": "test", "model": "primary", "reason": "network"},
        ]

    asyncio.run(run())


def test_real_text_delta_prevents_model_failover() -> None:
    async def run() -> None:
        primary = _Model("primary")
        fallback = _Model("fallback")
        called: list[str] = []

        def base_stream(model, _context, _options):
            called.append(model.id)
            stream = AssistantMessageEventStream()
            message = _message(model)

            async def produce() -> None:
                stream.push(EventStart(partial=message))
                stream.push(EventTextStart(content_index=0, partial=message))
                message.content = [TextContent(text="partial")]
                stream.push(EventTextDelta(content_index=0, delta="partial", partial=message))
                stream.fail(ProviderStreamError("mid-stream EOF", retryable=True))

            asyncio.create_task(produce())
            return stream

        stream = stream_with_failover(base_stream, primary, object(), None, [fallback])
        with pytest.raises(ProviderStreamError, match="mid-stream EOF"):
            _ = [event async for event in stream]

        assert called == ["primary"]

    asyncio.run(run())


def _stalling_provider(started: list, ended: list):
    """A provider whose stream announces itself and then never says more."""
    def base_stream(model, _context, _options):
        stream = AssistantMessageEventStream()

        async def produce() -> None:
            started.append(model.id)
            stream.push(EventStart(partial=_message(model)))
            try:
                await asyncio.Event().wait()
            finally:
                ended.append(model.id)

        stream.attach_producer(asyncio.ensure_future(produce()))
        return stream

    return base_stream


def test_a_stop_reaches_the_provider_request_under_the_failover_wrapper() -> None:
    from openprogram.models.providers.utils.event_stream import close_stream

    async def run() -> None:
        started: list[str] = []
        ended: list[str] = []
        stream = stream_with_failover(
            _stalling_provider(started, ended), _Model("primary"), object(), None, [_Model("fallback")],
        )
        first = await stream.__anext__()
        assert first.type == "start" and started == ["primary"]

        await close_stream(stream)  # what a stopped turn does with its response stream

        assert ended == ["primary"]  # the provider's own task ended with it
        assert started == ["primary"]  # and no fallback was tried for a stop

    asyncio.run(run())


def test_a_restarted_reply_can_still_fail_over() -> None:
    from openprogram.models.providers.types import EventRetry

    async def run() -> None:
        primary = _Model("primary")
        fallback = _Model("fallback")

        def base_stream(model, _context, _options):
            stream = AssistantMessageEventStream()
            message = _message(model)

            async def produce() -> None:
                stream.push(EventStart(partial=message))
                if model is primary:
                    stream.push(EventTextDelta(content_index=0, delta="partial", partial=message))
                    # The request layer sends it again and voids that text...
                    stream.push(EventRetry(attempt=2, reason="transport", restart=True))
                    # ...and the new attempt fails before any content.
                    stream.fail(ProviderStreamError("ReadError: lost", retryable=True))
                    return
                stream.push(EventDone(reason="stop", message=message))

            asyncio.create_task(produce())
            return stream

        stream = stream_with_failover(base_stream, primary, object(), None, [fallback])
        events = [event async for event in stream]
        assert events[-1].type == "done" and events[-1].message.model == "fallback"

    asyncio.run(run())
