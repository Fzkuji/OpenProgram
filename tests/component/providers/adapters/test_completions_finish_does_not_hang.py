"""finish_reason must end the turn even if SSE never closes.

Grok/xAI often keep the stream open after the last choices chunk
(waiting for usage / [DONE]). After we stopped cancelling the pending
read on poll timeout, that hang left the session running with the
text already on screen.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from openprogram.providers.openai_completions import openai_completions
from openprogram.providers.types import (
    Context,
    EventDone,
    Model,
    SimpleStreamOptions,
)


def _model():
    return Model(
        id="grok-test",
        name="Grok test",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def _text_chunk(text: str, finish=None, usage=None):
    return SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(
            delta=SimpleNamespace(
                reasoning_content=None,
                reasoning=None,
                content=text,
                tool_calls=None,
            ),
            finish_reason=finish,
        )],
    )


def _usage(n=3):
    return SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=n,
        total_tokens=10 + n,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )


class _HangAfter:
    """Yield listed chunks, then block forever on the next read."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.blocked = asyncio.Event()
        self.extra_reads = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        self.extra_reads += 1
        await self.blocked.wait()
        raise StopAsyncIteration


def _install_stream(monkeypatch, stream):
    class Completions:
        async def create(self, **params):
            return stream

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(openai_completions._openai, "AsyncOpenAI", Client)


def _consume(stream):
    options = SimpleStreamOptions(api_key="test-key")
    context = Context()

    async def run():
        started = time.monotonic()
        events = [
            event async for event in openai_completions.stream_simple(
                _model(), context, options,
            )
        ]
        return events, time.monotonic() - started

    return asyncio.run(run())


def test_finish_reason_with_usage_does_not_wait_for_close(monkeypatch):
    stream = _HangAfter([
        _text_chunk("不必改"),
        _text_chunk("", finish="stop", usage=_usage()),
    ])
    _install_stream(monkeypatch, stream)
    events, elapsed = _consume(stream)
    kinds = [getattr(e, "type", None) for e in events]
    assert "done" in kinds
    done = next(e for e in events if isinstance(e, EventDone) or getattr(e, "type", None) == "done")
    text = "".join(
        getattr(b, "text", "") for b in (done.message.content or [])
        if getattr(b, "type", None) == "text"
    )
    assert "不必改" in text
    assert elapsed < 1.0


def test_finish_reason_without_usage_drains_then_stops(monkeypatch):
    monkeypatch.setattr(openai_completions, "USAGE_DRAIN_S", 0.05)
    stream = _HangAfter([
        _text_chunk("不必改"),
        _text_chunk("", finish="stop"),
    ])
    _install_stream(monkeypatch, stream)
    events, elapsed = _consume(stream)
    kinds = [getattr(e, "type", None) for e in events]
    assert "done" in kinds
    assert elapsed < 2.0
