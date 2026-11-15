"""A Chat Completions stream that stops sending chunks is retried inside the provider.

A request whose response headers never come is retried the same way.

The scripted SDK stream yields its chunks and then waits forever, as a
stalled upstream does. With the stream idle bound shortened to a fraction
of a second, the attempt ends as a retryable ``StreamIdleTimeout``, the
provider's own retry loop shows a retry and re-sends, and the next stream
answers. A stall after visible text is not re-sent: that text was shown.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from openprogram.models.providers.adapters.openai_completions import openai_completions
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout

IDLE_S = 0.3


@pytest.fixture(autouse=True)
def _short_bound(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", str(IDLE_S))
    monkeypatch.setattr(openai_completions, "stream_backoff_seconds", lambda *a, **kw: 0.0)


def _model():
    return Model(id="deepseek-test", name="DeepSeek test", api="openai-completions",
                 provider="deepseek", base_url="https://api.deepseek.com/v1")


def _chunk(text=None, thinking=None, finish=None):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(
        delta=SimpleNamespace(reasoning_content=thinking, reasoning=None, content=text, tool_calls=None),
        finish_reason=finish,
    )])


class _Stream:
    """Yield ``chunks``; then stall forever when ``stall`` is set."""

    def __init__(self, chunks, stall=False):
        self._chunks = list(chunks)
        self._stall = stall
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._stall:
            await asyncio.Event().wait()
        raise StopAsyncIteration


# A request the endpoint takes and never answers: ``create()`` never returns.
_NO_HEADERS = object()


def _install(monkeypatch, streams):
    sent = []

    class Completions:
        async def create(self, **params):
            stream = streams.pop(0)
            sent.append(stream)
            if stream is _NO_HEADERS:
                await asyncio.Event().wait()
            return stream

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(openai_completions._openai, "AsyncOpenAI", Client)
    return sent


async def _consume():
    events = []
    async for event in openai_completions.stream_simple(_model(), Context(), SimpleStreamOptions(api_key="k")):
        events.append((time.monotonic(), event))
    return events


@pytest.mark.parametrize("before_stall", [[], [_chunk(thinking="half a thought")]],
                         ids=["before-first-chunk", "after-thinking"])
def test_a_stalled_stream_is_retried_and_answers(monkeypatch, before_stall):
    sent = _install(monkeypatch, [
        _Stream(before_stall, stall=True),
        _Stream([_chunk(text="answer", finish="stop")]),
    ])
    started = time.monotonic()
    events = asyncio.run(_consume())
    retry_at, retry = next((at, event) for at, event in events if event.type == "retry")
    assert IDLE_S <= retry_at - started < IDLE_S + 1.0
    assert retry.reason == "transport" and "StreamIdleTimeout" in retry.error
    done = events[-1][1]
    assert done.type == "done"
    assert [block.text for block in done.message.content if block.type == "text"] == ["answer"]
    assert len(sent) == 2 and sent[0].closed


def test_a_stall_after_visible_text_is_not_resent(monkeypatch):
    sent = _install(monkeypatch, [
        _Stream([_chunk(text="shown")], stall=True),
        _Stream([_chunk(text="never requested", finish="stop")]),
    ])
    with pytest.raises(StreamIdleTimeout):
        asyncio.run(_consume())
    assert len(sent) == 1 and sent[0].closed


def test_a_request_whose_headers_never_come_is_retried(monkeypatch):
    sent = _install(monkeypatch, [
        _NO_HEADERS,
        _Stream([_chunk(text="answer", finish="stop")]),
    ])
    started = time.monotonic()
    events = asyncio.run(_consume())
    retry_at, retry = next((at, event) for at, event in events if event.type == "retry")
    assert IDLE_S <= retry_at - started < IDLE_S + 1.0
    assert retry.reason == "transport" and "StreamIdleTimeout" in retry.error
    assert events[-1][1].type == "done"
    assert len(sent) == 2
