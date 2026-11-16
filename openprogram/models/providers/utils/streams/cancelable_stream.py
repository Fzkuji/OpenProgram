"""Poll a cancel signal while waiting for the next stream event.

Mirrors openai_codex: Stop must not wait for the next SSE token
(Vercel AI SDK / 0ms occupancy). async for event in stream only
notices cancel between chunks; mid-reasoning that is seconds.

Do NOT use asyncio.wait_for on __anext__. Timeout cancels the
pending read, which tears down httpx/OpenAI SSE iterators. Grok
thinking often has no chunk for >250ms; that produced a completed
assistant with empty content (result length 0).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Callable

CANCEL_POLL_S = 0.25


async def iter_until_cancelled(
    stream: Any,
    cancelled: Callable[[], bool],
    *,
    poll_s: float = CANCEL_POLL_S,
) -> AsyncIterator[Any]:
    """Yield events from stream until it ends or cancelled() is true.

    Polls the cancel signal every poll_s while a single __anext__
    stays outstanding. On cancel, returns so the caller async with
    can close the HTTP stream.
    """
    iterator = stream.__aiter__()
    pending = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            if cancelled():
                return
            done, _ = await asyncio.wait({pending}, timeout=poll_s)
            if not done:
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield event
            pending = asyncio.create_task(iterator.__anext__())
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
