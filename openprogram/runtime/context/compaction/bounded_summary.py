"""Bound text summarization without dropping any source on overflow.

A summary request runs as long as its stream keeps producing: it fails
only after :data:`SUMMARY_IDLE_S` seconds without an event, so a slow model
on a large conversation finishes while a stalled stream does not hold the
caller. A set ``signal`` closes the stream at once instead of waiting for
the next event.
"""
from __future__ import annotations

import asyncio
import contextlib

from openprogram.runtime.context.tokens import _text_tokens, estimate_history_tokens, real_context_window
from openprogram.models.providers.types import Context, SimpleStreamOptions, TextContent, UserMessage
from openprogram.models.providers.utils.overflow import is_context_overflow

#: Seconds a summary stream may go without an event before it fails.
SUMMARY_IDLE_S = 60.0


class SummaryTruncated(ValueError):
    """The summary stopped at its output cap: it is incomplete, so it fails."""
_POLL_S = 0.05


async def summary_reply(model, context, options: SimpleStreamOptions, *,
                        idle_s: float = SUMMARY_IDLE_S):
    """One summary request's final message, read off its stream.

    The stream is read in one task (its context variables are set and reset
    there) and that task is cancelled, closing the stream, as soon as
    ``options.signal`` is set or no event has arrived for ``idle_s`` seconds.
    """
    from openprogram.models import providers
    from openprogram.models.providers.streaming.stream import _extract_final

    loop = asyncio.get_running_loop()
    last_event = [loop.time()]

    async def read():
        final = None
        async for event in providers.stream_simple(model, context, options):
            last_event[0] = loop.time()
            final = _extract_final(event) or final
        return final

    reader = asyncio.ensure_future(read())
    signal = options.signal
    try:
        while not reader.done():
            await asyncio.wait([reader], timeout=_POLL_S)
            if reader.done():
                break
            if signal is not None and signal.is_set():
                raise asyncio.CancelledError()
            if loop.time() - last_event[0] > idle_s:
                raise TimeoutError(f'Summary stream produced nothing for {idle_s:.0f}s')
    finally:
        if not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
    final = reader.result()
    if final is None:
        raise RuntimeError('Stream completed without a final message')
    return final


async def summarize_text(text, model, options: SimpleStreamOptions, *, instruction: str,
                         max_tokens: int, remaining: list[int] | None = None) -> str:
    """Roll a bounded summary over every character; commit only on success.

    The mutable call allowance lets a request share one budget across evidence
    summaries and its final checkpoint. Provider overflow retries consume that
    same allowance. Authentication and other provider errors are not retried.
    Each request may run as long as its stream keeps producing
    (:func:`summary_reply`).
    """
    from openprogram.runtime.context.compaction.reactive import is_overflow_error
    from openprogram.models.usage import usage_scope

    window = real_context_window(model)
    # The caller's cap within the model's output cap, and never more than a
    # quarter of the window, so the source keeps room in a small window.
    output = min(max_tokens, model.max_tokens, max(1, window // 4))
    limit = window - output - max(256, window // 20) - _text_tokens(instruction)
    remaining = remaining if remaining is not None else [32]
    summary = ''
    position = 0
    source_cap = len(text)

    def context_for(chunk):
        # Prior output is data too: never upgrade a generated summary to system.
        prompt = (f'<previous-summary>\n{summary}\n</previous-summary>\n'
                  f'<source>\n{chunk}\n</source>')
        return Context(system_prompt=instruction, messages=[UserMessage(
            content=[TextContent(text=prompt)], timestamp=0)])

    while position < len(text):
        if options.signal is not None and options.signal.is_set():
            raise asyncio.CancelledError()
        lo, hi = 0, min(len(text) - position, source_cap)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_history_tokens(context_for(text[position:position + mid]).messages) <= limit:
                lo = mid
            else:
                hi = mid - 1
        if lo == 0:
            raise ValueError('No input capacity for summarization')
        if remaining[0] <= 0:
            raise ValueError('Summary request budget exhausted')
        remaining[0] -= 1
        chunk = text[position:position + lo]
        context = context_for(chunk)
        summary_options = SimpleStreamOptions(
            max_tokens=output, signal=options.signal, api_key=options.api_key,
            headers=options.headers, transport=options.transport,
            max_retry_delay_ms=options.max_retry_delay_ms,
        )
        try:
            with usage_scope(call_kind='summarize'):
                response = await summary_reply(model, context, summary_options)
        except Exception as exc:
            if not is_overflow_error(exc) or lo <= 1:
                raise
            source_cap = lo // 2
            continue
        if response.stop_reason == 'aborted' or (options.signal is not None and options.signal.is_set()):
            raise asyncio.CancelledError()
        if is_context_overflow(response):
            if lo <= 1:
                raise ValueError('Summary input exceeds provider context window')
            source_cap = lo // 2
            continue
        result = '\n'.join(b.text for b in response.content if isinstance(b, TextContent))
        if response.stop_reason == 'length':
            raise SummaryTruncated('Summary reached its output cap before it ended')
        if response.stop_reason in {'error', 'toolUse'} or not result.strip() or _text_tokens(result) > output:
            raise ValueError('Summarizer did not produce a complete bounded result')
        summary = result
        position += lo
    return summary
