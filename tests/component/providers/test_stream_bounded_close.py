"""A bounded close stops waiting at its bound and leaves the close running.

A stop closes the provider's stream with a bound (``close_stream`` with a
``timeout``). The close it starts cancels the producer once and waits for it;
a producer closing a socket holds that cancel off with a shield until the
round trip ends. When the bound is spent the caller goes on, and the close is
handed to the loop's owner, who finishes it (``settle_unwinding``). It is not
cancelled at the bound: that second cancel would reach the producer through
the close waiting on it and cut the socket close short.

See docs/reference/design/providers/reliability/error-and-timeout-mechanism.html.
"""
from __future__ import annotations

import asyncio
import time

from openprogram.models.providers.utils.event_stream import (
    AssistantMessageEventStream,
    close_stream,
    settle_unwinding,
)


def _stream_with_slow_close(close_s: float, closed: list[float]) -> tuple:
    stream = AssistantMessageEventStream()

    async def close_socket() -> None:
        await asyncio.sleep(close_s)
        closed.append(time.monotonic())

    async def run() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.shield(asyncio.ensure_future(close_socket()))

    producer = asyncio.ensure_future(run())
    stream.attach_producer(producer)
    return stream, producer


def test_a_close_longer_than_its_bound_is_finished_by_the_loop_owner():
    closed: list[float] = []

    async def scenario():
        stream, producer = _stream_with_slow_close(0.2, closed)
        await asyncio.sleep(0)
        began = time.monotonic()
        await close_stream(stream, timeout=0.05)
        waited = time.monotonic() - began
        still_closing = not producer.done()
        await settle_unwinding(grace=2.0)
        return waited, still_closing, producer

    waited, still_closing, producer = asyncio.run(scenario())

    assert waited < 0.15, f"the bounded close waited {waited:.2f}s"
    assert still_closing
    assert closed, "the provider's socket close was cut short"
    assert producer.cancelled()


def test_a_close_longer_than_the_owner_grace_is_cut_there():
    closed: list[float] = []

    async def scenario():
        stream, producer = _stream_with_slow_close(5.0, closed)
        await asyncio.sleep(0)
        await close_stream(stream, timeout=0.05)
        began = time.monotonic()
        await settle_unwinding(grace=0.1, bound=0.5)
        return time.monotonic() - began, producer

    settled, producer = asyncio.run(scenario())

    assert settled < 0.5, f"settling took {settled:.2f}s"
    assert producer.done()
    assert closed == []
