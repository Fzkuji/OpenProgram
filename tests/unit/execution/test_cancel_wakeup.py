"""A stop reaches the turn's loop and cuts its provider read at once.

The turn's signal is a plain Event (request options holding it are
deep-copied); what a stop must do is registered beside it
(``run_control.wakeups``) and run when the stop sets it, with no thread
and no poll on the way (Codex ``or_cancel(&token)``).
"""
from __future__ import annotations

import asyncio
import copy
import threading
import time

from openprogram.runtime.execution.run_control import wakeups
from openprogram.runtime.execution.run_control.tokens import CancellationToken


def test_a_token_stop_cuts_a_pending_read_within_milliseconds() -> None:
    from openprogram.runtime.agent.loop.lifecycle import _attach_cancellable_producer
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _wake_on_stop

    token = CancellationToken("s1", "e1")
    took: list[float] = []

    class _Stream:
        def attach_producer(self, producer) -> None:
            self.producer = producer

    async def turn() -> None:
        loop_cancel = asyncio.Event()
        done = _wake_on_stop(token.event, loop_cancel)
        stream = _Stream()
        _attach_cancellable_producer(stream, asyncio.Event().wait(), loop_cancel)
        await asyncio.sleep(0)
        stopped_at: list[float] = []

        def stop() -> None:
            # A stop from another thread, as the server's command handler.
            stopped_at.append(time.perf_counter())
            token.cancel()

        stopper = asyncio.get_running_loop().run_in_executor(None, stop)
        try:
            await stream.producer
        except asyncio.CancelledError:
            took.append(time.perf_counter() - stopped_at[0])
        await stopper
        done()

    asyncio.run(turn())

    assert took and took[0] < 0.005, took


def test_a_turn_starts_no_thread_for_its_stop() -> None:
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _wake_on_stop

    before = threading.active_count()

    async def turn() -> int:
        done = _wake_on_stop(threading.Event(), asyncio.Event())
        during = threading.active_count()
        done()
        return during

    assert asyncio.run(turn()) == before


def test_the_signal_stays_copyable_with_its_wakeups_registered() -> None:
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _wake_on_stop

    signal = threading.Event()

    async def turn() -> None:
        loop_cancel = asyncio.Event()
        done = _wake_on_stop(signal, loop_cancel)
        copy.deepcopy({"cancel": loop_cancel})
        assert wakeups.registered(signal) == 1
        done()
        assert wakeups.registered(signal) == 0

    asyncio.run(turn())


def test_firing_after_the_loop_closed_does_nothing() -> None:
    signal = threading.Event()
    loop = asyncio.new_event_loop()
    calls: list[int] = []
    handle = wakeups.register(signal, lambda: calls.append(1), loop=loop)
    loop.close()

    wakeups.set_and_fire(signal)

    assert calls == []
    wakeups.unregister(handle)


def test_a_signal_set_without_firing_is_still_seen() -> None:
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    signal = threading.Event()

    async def turn() -> bool:
        loop_cancel = asyncio.Event()
        done = loop_runner._wake_on_stop(signal, loop_cancel)
        signal.set()
        try:
            await asyncio.wait_for(loop_cancel.wait(), timeout=loop_runner._STOP_BACKSTOP_S * 3)
            return True
        finally:
            done()

    assert asyncio.run(turn())


def test_a_stop_before_the_producer_ran_still_ends_its_stream() -> None:
    """A stop that comes before the loop's first step cuts the producer
    before its body runs; the reader is not left waiting on the stream."""
    from openprogram.models.providers.utils.event_stream import EventStream
    from openprogram.runtime.agent.loop.lifecycle import _attach_cancellable_producer

    async def turn() -> list:
        stream = EventStream(is_done=lambda e: False, get_result=lambda e: [])
        stop = asyncio.Event()
        stop.set()
        _attach_cancellable_producer(stream, asyncio.Event().wait(), stop)
        async def read() -> list:
            return [event async for event in stream]

        events = await asyncio.wait_for(read(), 1.0)
        return [events, await asyncio.wait_for(stream.result(), 1.0)]

    assert asyncio.run(turn()) == [[], []]

