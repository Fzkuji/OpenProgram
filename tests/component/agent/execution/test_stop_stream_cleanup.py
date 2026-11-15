"""A stopped turn leaves nothing of its model stream on the loop it closes.

The stop goes through the public command while the provider's reply is being
read. The provider here behaves like the Codex adapter: its own task feeds
the stream, reads frames from an async generator, and that generator's close
waits on a shielded round trip, as a WebSocket close does.

See docs/reference/design/runtime/execution/control.html.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
import warnings

import pytest

from tests.component.agent.execution import test_agent_continuation_real as _real
from tests.component.agent.execution.test_agent_continuation_real import _chat, _command, _wait

# Shared pytest fixture, bound at module level so pytest collects it here.
real_agent_chat = _real.real_agent_chat

class _SocketLikeProvider:
    """Streams text until stopped; closing its frame source is a slow shielded round trip."""

    def __init__(self, close_s: float) -> None:
        self.close_s = close_s
        self.reading = threading.Event()
        self.closed: list[float] = []
        self.tasks: list[asyncio.Task] = []
        self.generators: list = []

    def install(self, harness, monkeypatch) -> None:
        monkeypatch.setattr(harness.provider, "stream_simple", self.stream_simple)

    def stream_simple(self, model, context, options=None):
        from openprogram.models.providers.types import AssistantMessage
        from openprogram.models.providers.utils.event_stream import AssistantMessageEventStream

        stream = AssistantMessageEventStream()
        output = AssistantMessage(
            content=[], api=model.api, provider=model.provider, model=model.id, timestamp=0,
        )

        async def close_socket() -> None:
            await asyncio.sleep(self.close_s)
            self.closed.append(time.monotonic())

        async def frames():
            try:
                number = 0
                while True:
                    await asyncio.sleep(0.01)
                    if options is not None and options.signal is not None and options.signal.is_set():
                        from openprogram.models.providers.utils.errors import StreamAborted

                        raise StreamAborted("stream cancelled by caller signal")
                    number += 1
                    yield f"{number}\n"
            finally:
                # A WebSocket close: the handshake runs in a task of its own
                # and the caller waits on it shielded.
                closing = asyncio.ensure_future(close_socket())
                self.tasks.append(closing)
                await asyncio.shield(closing)

        async def run() -> None:
            stream.push({"type": "start", "partial": output})
            # Mid-stream the adapter holds its blocks as plain dicts.
            block = {"type": "text", "text": ""}
            output.content.append(block)
            stream.push({"type": "text_start", "content_index": 0, "partial": output})
            source = frames()
            self.generators.append(source)
            try:
                async for delta in source:
                    block["text"] += delta
                    stream.push({"type": "text_delta", "content_index": 0, "delta": delta, "partial": output})
                    if len(block["text"]) > 20:
                        self.reading.set()
            except Exception as exc:
                # The adapter's own answer to the caller's signal.
                output.stop_reason = "aborted"
                output.error_message = str(exc)
                stream.push({"type": "error", "reason": "aborted", "error": output})
                stream.end(output)

        producer = asyncio.ensure_future(run())
        self.tasks.append(producer)
        stream.attach_producer(producer)
        return stream


@pytest.fixture
def loop_reports(monkeypatch):
    """What every event loop made during the test reported to its exception handler."""
    reports: list[str] = []
    new_event_loop = asyncio.new_event_loop

    def recording_loop():
        loop = new_event_loop()

        def handler(_loop, context):
            reports.append(f"{context.get('message')}: {context.get('exception')!r} "
                           f"{context.get('task') or context.get('asyncgen') or ''}")

        loop.set_exception_handler(handler)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", recording_loop)
    return reports


def _turn_loops_closed() -> bool:
    return not any(thread.name == "openprogram-turn-loop-close" for thread in threading.enumerate())


def _late_cancel_bridge(monkeypatch, delay_s: float, *, wake_producer: bool = True) -> None:
    """Deliver the stop to the turn's loop ``delay_s`` after the safe point can see it.

    A stop reaches the agent loop twice: the safe point polled between
    provider events reads the cancelled token, and the wakeup sets the loop's
    cancel event, whose own wakeup cancels the producer task. Which
    comes first is a race; this fixes the order the leak needed.
    ``wake_producer=False`` sets the loop's cancel event without its wakeup,
    so the provider reads its signal before the producer is cancelled.
    """
    import openprogram.runtime.conversation.turn_runtime.loop_runner as loop_runner

    def bridge(cancel_event, loop_cancel):
        from openprogram.runtime.execution.run_control import wakeups

        loop = asyncio.get_running_loop()
        turn_over = threading.Event()

        def wake():
            if not loop_cancel.is_set():
                loop_cancel.set()
                if wake_producer:
                    wakeups.fire(loop_cancel)

        def watch():
            while not (cancel_event.wait(0.01) or turn_over.is_set()):
                pass
            if cancel_event.is_set():
                time.sleep(delay_s)
                try:
                    loop.call_soon_threadsafe(wake)
                except RuntimeError:
                    pass

        threading.Thread(target=watch, daemon=True).start()
        return turn_over.set

    monkeypatch.setattr(loop_runner, "_wake_on_stop", bridge)


def _stop_mid_read(h, provider):
    execution = _chat(h)
    _wait(provider.reading.is_set, timeout=10)
    current = h.store.get_execution(execution.execution_id)
    stopped_at = time.monotonic()
    result = _command(h, "execution.cancel", current, "stop-mid-read")
    assert result["status"] in {"accepted", "applying", "applied"}
    _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "cancelled", timeout=5)
    took = time.monotonic() - stopped_at
    _wait(lambda: not h.server._is_run_active(h.session_id), timeout=10)
    _wait(_turn_loops_closed, timeout=15)
    return took


def _stop_and_collect(h, provider, caplog):
    """Stop mid-read, wait for the turn's loop to close, and report what it left.

    Returns the stop's duration, the provider tasks that never finished and
    the generators still running. The provider's references are dropped and
    the collector run, so a task destroyed pending is reported to its loop.
    """
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        took = _stop_mid_read(h, provider)
        unfinished = [repr(task) for task in provider.tasks if not task.done()]
        running = [repr(generator) for generator in provider.generators if generator.ag_running]
        provider.tasks.clear()
        provider.generators.clear()
        for _ in range(3):  # a generator chain is finalized one level a pass
            gc.collect()
    return took, unfinished, running


def _asyncio_errors(caplog) -> list[str]:
    """What asyncio logged for a loop without the recording handler."""
    return [record.getMessage() for record in caplog.records if record.name == "asyncio"]


@pytest.mark.parametrize(
    "close_s, bridge_delay_s",
    [
        # The close ends inside the bounded wait; the watcher's cancel arrives
        # while the stopped read is still unwinding (the Codex stop that leaked).
        (0.3, 0.15),
        # The same close, the two signals in whichever order they come.
        (0.3, None),
        # The close outlasts the bounded wait and is finished by the loop's owner.
        (0.8, 0.15),
        (0.8, None),
        # The provider answers the stop itself before the loop's cancel
        # arrives: the reply ends, and the generators it was read through are
        # abandoned to the loop's finalizer while the loop is being closed.
        (0.0, 0.0),
    ],
)
def test_a_stop_mid_read_leaves_no_task_or_generator_for_the_closed_loop(
    real_agent_chat, monkeypatch, loop_reports, caplog, close_s, bridge_delay_s,
):
    h = real_agent_chat
    provider = _SocketLikeProvider(close_s)
    provider.install(h, monkeypatch)
    if bridge_delay_s is not None:
        _late_cancel_bridge(monkeypatch, bridge_delay_s)

    took, unfinished, running = _stop_and_collect(h, provider, caplog)

    assert unfinished == []
    assert running == []
    assert loop_reports == []
    assert _asyncio_errors(caplog) == []
    # The stop did not wait for the provider's close.
    assert took < (min(close_s, 0.5) or 0.5), f"the stop took {took:.2f}s"
    if bridge_delay_s:
        # The cancel reached the provider before the stop signal did, so the
        # provider was not already closing: its close ran to its end.
        assert provider.closed, "the provider's socket close was cut short"


def test_a_reply_the_provider_aborts_itself_ends_with_typed_text(
    real_agent_chat, monkeypatch, loop_reports, caplog,
):
    """The provider answers the stop before the loop's cancel arrives: its
    aborted final message, cut off inside a text block, reaches the agent
    loop, which records it. The block is text content by then, not the
    adapter's working dict, so recording it raises no serializer warning."""
    h = real_agent_chat
    provider = _SocketLikeProvider(0.0)
    provider.install(h, monkeypatch)
    # The loop's cancel cuts the producer at once, so here it only sets the
    # signal: the provider reads it on its next frame and answers first.
    _late_cancel_bridge(monkeypatch, 0.0, wake_producer=False)
    finals = []

    import openprogram.runtime.agent.loop.loop as agent_loop

    durable = agent_loop._durable_message

    def recording(message):
        if getattr(message, "stop_reason", None) == "aborted":
            finals.append(message)
        return durable(message)

    monkeypatch.setattr(agent_loop, "_durable_message", recording)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _, unfinished, running = _stop_and_collect(h, provider, caplog)

    assert finals, "the provider's aborted final message never reached the loop"
    assert [type(block).__name__ for block in finals[0].content] == ["TextContent"]
    assert finals[0].content[0].text.startswith("1\n2\n")
    assert [str(item.message) for item in caught if "serializ" in str(item.message).lower()] == []
    assert unfinished == []
    assert running == []
    assert loop_reports == []
    assert _asyncio_errors(caplog) == []


def _close_loop_with(make_task):
    """Leave one task on a fresh loop, as a turn would, and close the loop the way a turn does."""
    import openprogram.runtime.conversation.turn_runtime.loop_runner as loop_runner

    loop = asyncio.new_event_loop()
    task = loop.create_task(make_task())
    loop.run_until_complete(asyncio.sleep(0))
    began = time.monotonic()
    loop_runner._close_turn_loop(loop)
    _wait(_turn_loops_closed, timeout=15)
    assert loop.is_closed()
    return task, time.monotonic() - began


def test_a_close_a_cancelled_task_left_running_ends_before_the_loop_closes(
    monkeypatch, loop_reports, caplog,
):
    """A task cancelled twice drops the shield over its socket close and ends;
    the close goes on as a task nobody waits for, and appears on the loop only
    after the teardown has cancelled what the turn left."""
    closed: list[float] = []
    children: list[asyncio.Task] = []

    async def close_socket() -> None:
        await asyncio.sleep(0.1)
        closed.append(time.monotonic())

    async def reader() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            children.append(asyncio.ensure_future(close_socket()))

    with caplog.at_level(logging.WARNING, logger="asyncio"):
        task, _ = _close_loop_with(reader)
        unfinished = [repr(child) for child in children if not child.done()]
        children.clear()
        gc.collect()

    assert task.cancelled()
    assert unfinished == []
    assert closed, "the loop closed under the socket close"
    assert loop_reports == []
    assert _asyncio_errors(caplog) == []


def test_a_task_that_ignores_its_cancel_is_named_when_the_loop_closes(monkeypatch, caplog):
    import openprogram.runtime.conversation.turn_runtime.loop_runner as loop_runner

    monkeypatch.setattr(loop_runner, "_LEFT_TASKS_BOUND_S", 0.1)
    monkeypatch.setattr(loop_runner, "_CUT_TASKS_WAIT_S", 0.1)

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                pass

    with caplog.at_level(logging.WARNING, logger=loop_runner.__name__):
        task, took = _close_loop_with(stubborn)

    task._log_destroy_pending = False  # reported below, by name
    assert took < 2.0, f"closing the loop took {took:.2f}s"
    warned = [record.getMessage() for record in caplog.records]
    assert len(warned) == 1
    assert "1 task(s) still running" in warned[0] and "stubborn" in warned[0]
