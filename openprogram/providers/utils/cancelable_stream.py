"""Poll a cancel signal while waiting for the next stream event.

Mirrors openai_codex: Stop must not wait for the next SSE token
(Vercel AI SDK / 0ms occupancy). async for event in stream only
notices cancel between chunks; mid-reasoning that is seconds.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

CANCEL_POLL_S = 0.25


async def iter_until_cancelled(
    stream: Any,
    cancelled: Callable[[], bool],
    *,
    poll_s: float = CANCEL_POLL_S,
) -> AsyncIterator[Any]:
    """Yield events from stream until it ends or cancelled() is true.

    Waits for the next iterator event with a poll_s timeout so a
    cancel signal is observed without waiting for the next token. On
    cancel, returns (stops iteration) so the caller async with
    can close the HTTP stream.
    """
    iterator = stream.__aiter__()
    while True:
        if cancelled():
            return
        try:
            event = await asyncio.wait_for(
                iterator.__anext__(), timeout=poll_s,
            )
        except asyncio.TimeoutError:
            continue
        except StopAsyncIteration:
            return
        yield event
