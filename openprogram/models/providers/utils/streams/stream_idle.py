"""The idle bound of a streamed model request: no event for N seconds ends the attempt.

A reply that stops arriving without the connection failing (a stalled
upstream, a proxy holding a half-open socket) would otherwise be waited on
until the socket's own read timeout, minutes later, or forever when the
transport keeps the socket alive. Every streamed request is therefore read
under one bound: when no event arrives for :func:`event_idle_timeout_s`
seconds, the attempt ends with :class:`StreamIdleTimeout`, a transport error
the retry policy re-sends (``stream_retry`` and ``errors.classify_error``
both list it as transport).

The bound counts events, not bytes: keepalive pings and comment lines keep
a socket alive without saying anything, so they do not reset it. The value
follows the peers that read the same backends (Codex CLI's
``stream_idle_timeout_ms`` and Claude Code's event-level stream watchdog,
both 300 s) and stays above the longest silence a healthy reply shows (see
``docs/reference/design/providers/reliability/error-and-timeout-mechanism.html``).

The pending read is never cancelled to poll: cancelling and restarting an
SDK's stream read breaks its iterator. It is cancelled only once, when the
bound fires, so the stalled provider stream is torn down before the error
is raised. A caller's cancellation reaches the wait at once.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

# How long a stalled provider read is given to unwind after the bound fires.
_UNWIND_S = 1.0


class StreamIdleTimeout(Exception):
    """Nothing, or nothing but keepalives, arrived within the stream's idle bound."""


def event_idle_timeout_s() -> float:
    """Seconds a streamed reply may go without an event (``OPENPROGRAM_STREAM_IDLE_TIMEOUT_S``).

    ``0`` or a negative value turns the bound off.
    """
    raw = os.environ.get("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", "")
    try:
        return float(raw) if raw.strip() else 300.0
    except ValueError:
        return 300.0


def bounds_own_stream_idle(provider: Any) -> bool:
    """Whether ``provider`` reads each attempt under its own idle bound.

    Such a provider (the Codex transport, whose budget sits inside its retry
    loop) is not bounded a second time from outside.
    """
    return bool(getattr(provider, "bounds_own_stream_idle", False))


async def idle_bounded(
    events: Any,
    idle_s: float | None,
    *,
    label: str = "model stream",
) -> AsyncIterator[Any]:
    """Yield ``events`` until they end; raise :class:`StreamIdleTimeout` after ``idle_s`` of silence."""
    iterator = events.__aiter__()
    if not idle_s or idle_s <= 0:
        async for event in iterator:
            yield event
        return
    pending: asyncio.Future | None = None
    try:
        while True:
            pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=idle_s)
            if not done:
                stalled, pending = pending, None
                _discard(stalled)
                await asyncio.wait({stalled}, timeout=_UNWIND_S)
                raise StreamIdleTimeout(f"no {label} event for {idle_s:g}s")
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield event
    finally:
        # A caller's cancel or close: the read in flight stops, unawaited.
        if pending is not None:
            _discard(pending)


async def idle_bounded_open(
    awaitable: Any,
    idle_s: float | None,
    *,
    label: str = "model stream",
) -> Any:
    """Await the opening of a streamed request; raise :class:`StreamIdleTimeout` after ``idle_s``.

    The wait for the response headers is silence too: a gateway that takes
    the request and never answers is cut at the same bound as one that stops
    mid-reply. On expiry the open is cancelled, closing its connection.
    """
    if not idle_s or idle_s <= 0:
        return await awaitable
    timeout = asyncio.timeout(idle_s)
    try:
        async with timeout:
            return await awaitable
    except TimeoutError:
        if not timeout.expired():
            raise
        raise StreamIdleTimeout(f"no {label} response for {idle_s:g}s") from None


def _discard(task: asyncio.Future) -> None:
    """Cancel ``task``; whatever it ends with is not reported."""
    task.cancel()
    task.add_done_callback(lambda done: done.cancelled() or done.exception())


__all__ = [
    "StreamIdleTimeout",
    "bounds_own_stream_idle",
    "event_idle_timeout_s",
    "idle_bounded",
    "idle_bounded_open",
]
