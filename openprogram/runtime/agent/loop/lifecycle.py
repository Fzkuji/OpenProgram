"""Agent event stream lifecycle: stream creation, job activity heartbeats, interruption and cancellable producers."""
from __future__ import annotations

import asyncio
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.runtime.agent.loop.types import (
    AgentEvent,
    AgentMessage,
)
import logging

_log = logging.getLogger(__name__)


def _create_agent_stream() -> EventStream[AgentEvent, list[AgentMessage]]:
    return EventStream(
        is_done=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages if e.type == "agent_end" else [],
    )


def _record_job_activity(kind: str) -> None:
    try:
        from openprogram.runtime.agent.engine_link import record_job_activity
        record_job_activity(kind)
    except Exception:
        # Job activity is best-effort bookkeeping for the idle watchdog.
        _log.debug("job activity %r not recorded", kind, exc_info=True)


def _job_operation_timeout(declared: float | None) -> float | None:
    from openprogram.runtime.agent.engine_link import job_operation_timeout
    return job_operation_timeout(declared)


def _finish_interrupted_stream(stream, exc, messages, cancel_event) -> None:
    from openprogram.host.cancellation import CancelledError, check_cancelled
    from openprogram.models.providers.utils.errors import ExecInterrupt

    cancelled = isinstance(exc, (CancelledError, asyncio.CancelledError)) or bool(
        cancel_event is not None and cancel_event.is_set()
    )
    if isinstance(exc, ExecInterrupt) and not cancelled:
        from openprogram.runtime.agent.engine_link import worker_stopping
        if worker_stopping():
            if not stream._result_event.is_set():
                stream.fail(exc)
            return
        # A synchronous callback can observe the owner's cancellation before
        # this event loop has delivered its asynchronously bridged signal.
        try:
            check_cancelled()
        except CancelledError:
            cancelled = True
    if isinstance(exc, (CancelledError, asyncio.CancelledError, ExecInterrupt)) and cancelled:
        if not stream._result_event.is_set():
            stream.end(messages)
    elif isinstance(exc, ExecInterrupt):
        if not stream._result_event.is_set():
            stream.fail(exc)
    else:
        raise exc



def _attach_cancellable_producer(stream, coroutine, cancel_event):
    """Interrupt pending I/O even when a provider/tool emits no events."""
    producer = asyncio.ensure_future(coroutine)
    stream.attach_producer(producer)
    if cancel_event is None:
        return

    from openprogram.runtime.execution.run_control import wakeups

    def cut() -> None:
        if not producer.done():
            producer.cancel()

    async def finish_cut() -> None:
        # A producer that ignored its first cancel gets a second one.
        done, _ = await asyncio.wait({producer}, timeout=1.0)
        if not done:
            producer.cancel()

    def on_stop() -> None:
        if producer.done():
            return
        cut()
        asyncio.ensure_future(finish_cut())

    # The signal is part of request option snapshots, so nothing is attached
    # to it (deep-copying a pending Event is invalid): the producer's cut is
    # registered beside it and runs when it is fired (``run_control.wakeups``).
    handle = wakeups.register(cancel_event, on_stop, loop=asyncio.get_running_loop())

    async def backstop() -> None:
        # Only for code that sets the signal without firing it.
        while not cancel_event.is_set():
            await asyncio.sleep(_STOP_BACKSTOP_S)
        on_stop()

    watcher = asyncio.create_task(backstop())
    if cancel_event.is_set():
        on_stop()

    def done(task):
        wakeups.unregister(handle)
        watcher.cancel()
        ended = getattr(stream, "_result_event", None)
        if task.cancelled() and ended is not None and not ended.is_set():
            # Cut before its body ran (a stop that came before the loop's
            # first step): its own handler never ended the stream, and the
            # reader would wait on it for good. Nothing ran, so nothing is new.
            stream.end([])

    producer.add_done_callback(done)


#: A stop cuts the producer when it is fired; this check catches only code
#: that sets the signal without firing it.
_STOP_BACKSTOP_S = 0.5
