"""iter_until_cancelled must notice cancel without waiting for the next chunk."""

from __future__ import annotations

import asyncio
import time

from openprogram.providers.utils.cancelable_stream import iter_until_cancelled


class _BlockingStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(30)
        return {"type": "should-not-arrive"}


def test_iter_until_cancelled_returns_when_signal_set():
    signal = asyncio.Event()

    async def run() -> list:
        signal.set()
        started = time.monotonic()
        events = []
        async for event in iter_until_cancelled(
            _BlockingStream(), signal.is_set, poll_s=0.05,
        ):
            events.append(event)
        elapsed = time.monotonic() - started
        return events, elapsed

    events, elapsed = asyncio.run(run())
    assert events == []
    assert elapsed < 1.0


def test_iter_until_cancelled_yields_until_cancel():
    signal = asyncio.Event()
    remaining = ["a", "b"]

    class _TwoThenBlock:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if remaining:
                return remaining.pop(0)
            await asyncio.sleep(30)
            return "late"

    async def run() -> list:
        events = []
        agen = iter_until_cancelled(_TwoThenBlock(), signal.is_set, poll_s=0.05)
        async for event in agen:
            events.append(event)
            if event == "b":
                signal.set()
        return events

    assert asyncio.run(run()) == ["a", "b"]


def test_iter_until_cancelled_keeps_slow_first_chunk():
    """A first token slower than poll_s must still arrive.

    wait_for would cancel __anext__ on timeout and kill the SSE
    iterator — that is the empty completed Grok reply.
    """

    class _SlowFirst:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.reads_cancelled = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.started.set()
            try:
                await self.release.wait()
                return "hello"
            except asyncio.CancelledError:
                self.reads_cancelled += 1
                raise

    async def run():
        stream = _SlowFirst()
        agen = iter_until_cancelled(stream, lambda: False, poll_s=0.01)
        pull = asyncio.create_task(agen.__anext__())
        await stream.started.wait()
        for _ in range(4):
            done, _ = await asyncio.wait({pull}, timeout=0.02)
            assert not done
        stream.release.set()
        event = await pull
        return event, stream.reads_cancelled

    event, cancelled = asyncio.run(run())
    assert event == "hello"
    assert cancelled == 0

