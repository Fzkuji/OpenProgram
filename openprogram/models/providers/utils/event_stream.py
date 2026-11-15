"""
Generic event stream implementation — mirrors packages/ai/src/utils/event-stream.ts

Provides an async-iterable stream with a terminal result value.
"""
from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any, Callable, Generic, TypeVar
import logging

_log = logging.getLogger(__name__)

T = TypeVar("T")  # event type
R = TypeVar("R")  # result type


class EventStream(Generic[T, R]):
    """
    An async-iterable stream of events T with a terminal result R.

    Mirrors the TypeScript EventStream class:
    - push(event) — emit an event
    - end(result) — signal completion and store the result
    - result() — await the final result
    - async iteration — yields events until end() is called
    """

    def __init__(
        self,
        is_done: Callable[[T], bool] | None = None,
        get_result: Callable[[T], R] | None = None,
    ) -> None:
        self._is_done = is_done
        self._get_result = get_result
        self._queue: asyncio.Queue[T | _Sentinel] = asyncio.Queue()
        self._result: R | None = None
        self._result_event = asyncio.Event()
        self._error: BaseException | None = None
        self._producer_task: asyncio.Task | None = None

    def attach_producer(self, task: asyncio.Task) -> None:
        self._producer_task = task

    async def cancel_producer(self) -> None:
        task = self._producer_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except BaseException as exc:
            # The producer was cancelled on purpose; how it ended belongs to
            # the stream's consumer, not to the code cancelling it.
            if not isinstance(exc, asyncio.CancelledError):
                _log.debug("producer ended with an error while cancelling", exc_info=True)

    def push(self, event: T) -> None:
        """Push an event into the stream.

        Providers that emit plain dicts (keyed on "type") get normalized into
        the matching typed AssistantMessageEvent — consumers index events via
        attribute access and crash on dict inputs otherwise.
        """
        if isinstance(event, dict) and "type" in event:
            normalized = _dict_to_assistant_event(event)
            if normalized is not None:
                event = normalized  # type: ignore[assignment]
        self._queue.put_nowait(event)
        if self._is_done and self._is_done(event):
            result = self._get_result(event) if self._get_result else None
            self._result = result
            self._result_event.set()
            self._queue.put_nowait(_SENTINEL)

    def end(self, result: R) -> None:
        """Signal stream completion with the final result."""
        self._result = result
        self._result_event.set()
        self._queue.put_nowait(_SENTINEL)

    def fail(self, error: BaseException) -> None:
        """Signal stream failure with an exception."""
        self._error = error
        self._result_event.set()
        self._queue.put_nowait(_SENTINEL)

    async def result(self) -> R:
        """Await the final result of the stream."""
        await self._result_event.wait()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]

    def __aiter__(self) -> "EventStream[T, R]":
        return self

    async def __anext__(self) -> T:
        item = await self._queue.get()
        if isinstance(item, _Sentinel):
            if self._error is not None:
                raise self._error
            raise StopAsyncIteration
        return item


#: Tasks a stopped reader left unwinding, per loop. A reader that stops does
#: not wait for the provider to finish closing; whoever closes the loop does,
#: through :func:`settle_unwinding`, before the loop shuts its async
#: generators down.
_unwinding: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Future]]" = (
    weakref.WeakKeyDictionary()
)


def _retrieve(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


def leave_unwinding(task: asyncio.Future) -> None:
    """Hand ``task``, still unwinding after its reader stopped, to the loop's owner.

    However it ends is not reported: the reader already has its outcome.
    """
    task.add_done_callback(_retrieve)
    if task.done():
        return
    tasks = _unwinding.setdefault(task.get_loop(), set())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def settle_unwinding(*, grace: float = 2.0, bound: float = 1.0) -> None:
    """Finish the tasks stopped readers left on the running loop.

    They are given ``grace`` seconds to end on their own (a socket close is a
    round trip); what is still running then is cancelled and awaited for at
    most ``bound`` seconds more.
    """
    deadline = time.monotonic() + grace
    while True:
        # A task that ends may leave another: read the set again each round.
        left = set(_unwinding.get(asyncio.get_running_loop(), ()))
        left.discard(asyncio.current_task())
        if not left:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.wait(left, timeout=remaining)
    for task in left:
        task.cancel()
    await asyncio.wait(left, timeout=bound)


async def close_stream(stream, iterator=None, *, timeout=None):
    """Close the owned producer, with bounded cleanup for an explicit stop.

    With a ``timeout`` the caller waits that long at most. A close still
    running then is left to the loop's owner (:func:`leave_unwinding`), not
    cancelled: a second cancel would reach the producer through the close
    waiting on it and cut the provider's own close short. The caller goes on,
    and the close is finished before the loop is.
    """
    close = getattr(stream, "cancel_producer", None)
    if not callable(close):
        close = getattr(iterator if iterator is not None else stream, "aclose", None)
    if not callable(close):
        return
    current = asyncio.current_task()
    try:
        if timeout is None:
            await close()
        else:
            cleanup = asyncio.ensure_future(close())
            try:
                await asyncio.wait({cleanup}, timeout=timeout)
            finally:
                # Also when this wait is itself cancelled: the close goes on.
                leave_unwinding(cleanup)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Closing is cleanup after the caller already stopped consuming.
        _log.debug("stream close failed", exc_info=True)
    if current is not None and current.cancelling():
        raise asyncio.CancelledError()


class _Sentinel:
    """Sentinel value to signal stream end."""


_SENTINEL = _Sentinel()


# Import types for AssistantMessageEventStream
from ..types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventStart,
    EventTextStart,
    EventTextDelta,
    EventTextEnd,
    EventStructuredOutputRetry,
    EventRetry,
    EventStructuredOutputEnd,
    EventThinkingStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventToolCallStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventServerToolStart,
    EventServerToolEnd,
    EventDone,
    EventError,
)

_ASSISTANT_EVENT_CLASSES = {
    "start": EventStart,
    "text_start": EventTextStart,
    "text_delta": EventTextDelta,
    "text_end": EventTextEnd,
    "structured_output_retry": EventStructuredOutputRetry,
    "retry": EventRetry,
    "structured_output_end": EventStructuredOutputEnd,
    "thinking_start": EventThinkingStart,
    "thinking_delta": EventThinkingDelta,
    "thinking_end": EventThinkingEnd,
    "toolcall_start": EventToolCallStart,
    "toolcall_delta": EventToolCallDelta,
    "toolcall_end": EventToolCallEnd,
    "servertool_start": EventServerToolStart,
    "servertool_end": EventServerToolEnd,
    "done": EventDone,
    "error": EventError,
}


def _dict_to_assistant_event(event: dict[str, "Any"]) -> "AssistantMessageEvent | None":
    """Convert a provider-emitted dict event into its typed AssistantMessageEvent.

    Providers historically emitted ``{"type": ..., ...}`` dicts. Consumers use
    attribute access (``event.type``, ``event.partial``) and crash on dicts,
    so we validate the payload via the matching Pydantic model. Unknown
    ``type`` values or validation errors fall through — the caller keeps the
    raw dict and the downstream failure mode stays identical.
    """
    cls = _ASSISTANT_EVENT_CLASSES.get(event.get("type"))
    if cls is None:
        return None
    try:
        return cls.model_validate(event)
    except Exception:
        return None


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    """
    Specialized EventStream for AssistantMessageEvent -> AssistantMessage.
    Mirrors AssistantMessageEventStream in TypeScript.
    """
    
    def __init__(self):
        def is_done(event: AssistantMessageEvent) -> bool:
            return event.type == "done" or event.type == "error"
        
        def get_result(event: AssistantMessageEvent) -> AssistantMessage:
            if event.type == "done":
                return event.message
            elif event.type == "error":
                return event.error
            raise RuntimeError(f"Unexpected event type for final result: {event.type}")
        
        super().__init__(is_done, get_result)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    """
    Factory function for AssistantMessageEventStream.
    Mirrors createAssistantMessageEventStream() in TypeScript.
    """
    return AssistantMessageEventStream()
