"""The Gemini subscription adapter bounds each attempt's read itself.

Its retry loop sleeps between attempts, sometimes for as long as the server
asks. That sleep is not a stall: read through the stream chokepoint, a
server-directed wait longer than the idle bound must still answer, not end
as ``StreamIdleTimeout``. Response headers or data lines that never come
end the attempt at the bound, and a wait beyond the Retry-After cap goes up
at once as the rate-limit error. The bound is shortened to a fraction of a
second.
"""
from __future__ import annotations

import asyncio
import importlib
import time

import pytest

from openprogram.models.providers.catalog.register import _load_builtin_providers
from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, UserMessage
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout

gmod = importlib.import_module(
    "openprogram.models.providers.adapters.google_gemini_cli.google_gemini_cli")

IDLE_S = 0.3
_ANSWER = 'data: {"candidates": [{"content": {"parts": [{"text": "answer"}]}, "finishReason": "STOP"}]}'


@pytest.fixture(autouse=True)
def _short_bound(monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", str(IDLE_S))


def _model() -> Model:
    return Model(id="gemini-test", name="Gemini test", api="google-gemini-cli",
                 provider="gemini-subscription", base_url="https://gemini.invalid")


def _context() -> Context:
    return Context(messages=[UserMessage(content="hi", timestamp=0)])


class _Response:
    def __init__(self, status=200, body=b"", lines=(), stall=False):
        self.status_code = status
        self.headers: dict = {}
        self._body = body
        self._lines = list(lines)
        self._stall = stall

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._stall:
            await asyncio.Event().wait()


_NO_HEADERS = object()


class _Opening:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        if self._response is _NO_HEADERS:
            await asyncio.Event().wait()
        return self._response

    async def __aexit__(self, *exc):
        return False


def _install(monkeypatch, responses):
    sent = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers=None, content=None):
            response = responses.pop(0)
            sent.append(response)
            return _Opening(response)

    monkeypatch.setattr(gmod, "build_async_client", lambda **_kwargs: Client())
    return sent


async def _through_chokepoint():
    provider = _load_builtin_providers()["gemini-subscription"]
    events = []
    async for event in stream_simple_with_provider(
        provider, _model(), _context(), SimpleStreamOptions(api_key="k", project_id="p"),
    ):
        events.append((time.monotonic(), event))
    return events


async def _adapter_only():
    stream = gmod.stream_google_gemini_cli(_model(), _context(), {"api_key": "k", "project_id": "p"})
    async for _ in stream:
        pass


def test_a_server_directed_wait_longer_than_the_bound_answers(monkeypatch):
    # The hint is 1 s (+1 s margin): far beyond the 0.3 s bound.
    sent = _install(monkeypatch, [
        _Response(429, b'{"error": "please retry in 1s"}'),
        _Response(lines=[_ANSWER]),
    ])
    started = time.monotonic()
    events = asyncio.run(_through_chokepoint())
    done_at, done = events[-1]
    assert done.type == "done", done
    assert done_at - started >= 2.0
    assert [block.text for block in done.message.content if block.type == "text"] == ["answer"]
    assert len(sent) == 2


@pytest.mark.parametrize("response", [_NO_HEADERS, _Response(lines=[], stall=True)],
                         ids=["no-headers", "no-data-line"])
def test_a_silent_attempt_ends_at_the_bound(monkeypatch, response):
    _install(monkeypatch, [response])
    started = time.monotonic()
    with pytest.raises(StreamIdleTimeout):
        asyncio.run(_adapter_only())
    assert IDLE_S <= time.monotonic() - started < IDLE_S + 1.5


def test_a_wait_beyond_the_retry_after_cap_goes_up_at_once(monkeypatch):
    sent = _install(monkeypatch, [
        _Response(429, b'{"error": "quota will reset after 4000s"}'),
        _Response(lines=[_ANSWER]),
    ])
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="HTTP 429"):
        asyncio.run(_adapter_only())
    assert time.monotonic() - started < 1.0
    assert len(sent) == 1
