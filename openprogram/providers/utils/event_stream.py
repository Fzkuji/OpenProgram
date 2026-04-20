"""
Generic event stream implementation — mirrors packages/ai/src/utils/event-stream.ts

Provides an async-iterable stream with a terminal result value.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Generic, TypeVar

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
        self._error: Exception | None = None

    def push(self, event: T) -> None:
        """Push an event into the stream."""
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

    def fail(self, error: Exception) -> None:
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


class _Sentinel:
    """Sentinel value to signal stream end."""


_SENTINEL = _Sentinel()


# Import types for AssistantMessageEventStream
from ..types import AssistantMessage, AssistantMessageEvent


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
