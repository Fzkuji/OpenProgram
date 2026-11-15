"""The time budgets a Codex reply is read under, whatever carries it.

One reply, read a piece at a time (an SSE line over HTTP, a frame over the
WebSocket), is bounded three ways, whichever trips first:

* no bytes at all for ``STREAM_IDLE_TIMEOUT_S`` — a dead connection;
* no real data for ``STREAM_DATA_STALL_TIMEOUT_S`` — the stream idle bound
  every streamed request is read under (``utils/stream_idle.py``): a stream
  that stalls, or only sends keepalives;
* the optional total ``STREAM_TOTAL_TIMEOUT_S`` and the caller's own
  deadline (``runtime.exec(timeout_s=…)``).

The caller's cancel signal (the Stop button) is polled at 4 Hz without
cancelling the read in flight: cancelling and restarting a network read on
every poll made a model pause longer than the poll look like the server
closing the stream.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from openprogram.models.providers.utils.deadline import remaining as deadline_remaining
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout
from openprogram.models.providers.utils.timeouts import (
    STREAM_DATA_STALL_TIMEOUT_S,
    STREAM_IDLE_TIMEOUT_S,
    STREAM_TOTAL_TIMEOUT_S,
)

T = TypeVar("T")

_SIGNAL_POLL_S = 0.25


class StreamTotalTimeout(Exception):
    """One stream ran past its total budget or the caller's deadline."""


class ReadBudget:
    """The budgets of one reply; :meth:`read` reads its next piece under them."""

    def __init__(self, signal: Any = None, *, label: str = "stream") -> None:
        self.signal = signal
        self.label = label
        start = time.monotonic()
        self._deadline = start + STREAM_TOTAL_TIMEOUT_S
        self._last_activity = start
        self._last_data = start

    def saw_data(self) -> None:
        """A real event arrived (not a keepalive): the stall clock restarts."""
        self._last_data = time.monotonic()

    def _cancelled(self) -> bool:
        return (
            self.signal is not None
            and callable(getattr(self.signal, "is_set", None))
            and self.signal.is_set()
        )

    async def read(self, next_piece: Callable[[], Awaitable[T]]) -> T:
        """Await ``next_piece()`` within the budgets; any piece counts as activity."""
        task = asyncio.ensure_future(next_piece())
        try:
            while True:
                if self._cancelled():
                    from openprogram.models.providers.utils.errors import StreamAborted

                    raise StreamAborted("stream cancelled by caller signal")
                now = time.monotonic()
                if now >= self._deadline:
                    raise StreamTotalTimeout(
                        f"{self.label} total budget {STREAM_TOTAL_TIMEOUT_S}s exceeded"
                    )
                idle_left = STREAM_IDLE_TIMEOUT_S - (now - self._last_activity)
                stall_left = STREAM_DATA_STALL_TIMEOUT_S - (now - self._last_data)
                if idle_left <= 0:
                    raise StreamIdleTimeout(f"no {self.label} bytes for {STREAM_IDLE_TIMEOUT_S}s")
                if stall_left <= 0:
                    raise StreamIdleTimeout(
                        f"no {self.label} data event for {STREAM_DATA_STALL_TIMEOUT_S}s"
                    )
                wait = min(idle_left, stall_left, self._deadline - now)
                caller_left = deadline_remaining()
                if caller_left is not None:
                    if caller_left <= 0:
                        raise StreamTotalTimeout("caller deadline (exec timeout_s) exceeded mid-stream")
                    wait = min(wait, caller_left)
                if self.signal is not None:
                    wait = min(wait, _SIGNAL_POLL_S)
                try:
                    piece = await asyncio.wait_for(asyncio.shield(task), timeout=wait)
                except asyncio.TimeoutError:
                    continue
                self._last_activity = time.monotonic()
                return piece
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
