"""
Unified streaming functions — mirrors packages/ai/src/stream.ts

Provides stream(), complete(), stream_simple(), complete_simple().
"""
from __future__ import annotations

from contextlib import contextmanager
import inspect
import time
from typing import AsyncGenerator, Awaitable, Callable

from ..catalog.api_registry import get_api_provider
from .budget import BudgetedRequest
from ..utils import request_repair
from .first_token import FirstToken
from .resend import Resend, reached_provider
from ..utils.stream_idle import bounds_own_stream_idle, event_idle_timeout_s, idle_bounded
from openprogram.models.usage.prefix_fingerprint import check_request, settle_check
from openprogram.models.usage.request import RequestReceipt
from ..recording.capture import RequestSlot, bind_slot
from ..settings.env_api_keys import resolve_provider_key
from ..types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    EventStart,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ThinkingContent,
)
import logging

_log = logging.getLogger(__name__)


@contextmanager
def _job_operation_deadline():
    """Bind one provider request to the claimed job's remaining time."""
    from openprogram.state.execution_facts import (
        current_job_operation_timeout,
        record_current_job_activity,
    )
    from openprogram.models.providers.utils.deadline import (
        get_deadline,
        reset_deadline,
        set_deadline,
    )

    timeout = current_job_operation_timeout(None, preemptibility="async")
    deadline = None if timeout is None else time.monotonic() + timeout
    outer = get_deadline()
    if outer is not None and (deadline is None or outer < deadline):
        deadline = outer
    token = set_deadline(deadline) if deadline is not None else None
    record_current_job_activity("operation_start")
    try:
        yield record_current_job_activity
    finally:
        if token is not None:
            reset_deadline(token)


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """
    Stream a response with unified reasoning options.
    Automatically resolves API key from environment if not provided.
    Mirrors streamSimple() from TypeScript.
    """
    from openprogram.models.auth.provider_policy import require_supported_provider
    require_supported_provider(model.provider)
    with _job_operation_deadline() as record_activity:
        provider = get_api_provider(model.api)
        async for event in stream_simple_with_provider(provider, model, context, options):
            record_activity("provider_data")
            yield event


async def stream_simple_with_provider(
    provider,
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
    get_api_key: Callable[[str], str | None | Awaitable[str | None]] | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """Stream through an already-resolved request-scoped provider snapshot."""
    from openprogram.models.auth.provider_policy import require_supported_provider
    require_supported_provider(model.provider)
    opts = options or SimpleStreamOptions()

    if provider is None:
        raise ValueError(f"No stream function registered for API: {model.api!r}")

    # Reserve budget BEFORE credentials or network: a denied call must never
    # resolve a key or open a socket. Returns None for unbudgeted callers.
    budget = BudgetedRequest.begin(model, context, opts, provider)
    receipt = None
    try:
        receipt = RequestReceipt.begin(model, opts, provider_context=context, provider=provider, budget=budget)
        if budget is not None:
            opts = budget.clamp(opts, model)
        if receipt is not None:
            opts = receipt.clamp(opts, model)

        if get_api_key is not None:
            key_result = get_api_key(model.provider)
            if inspect.isawaitable(key_result):
                key_result = await key_result
            if key_result:
                opts = opts.model_copy(update={"api_key": key_result})

        # Auto-resolve API key if not set. resolve_provider_key reads the
        # AuthStore (the single key source — no env vars, no config.json).
        if not opts.api_key and getattr(provider, "requires_credentials", True):
            opts = opts.model_copy(
                update={"api_key": resolve_provider_key(model.provider)},
            )
    except BaseException:
        if budget is not None:
            budget.release()
        if receipt is not None:
            receipt.release()
        raise

    async for event in _delivered(provider.stream_simple, provider, model, context, opts, budget, receipt):
        yield event


async def complete_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessage:
    """
    Get a complete (non-streaming) response.
    Mirrors completeSimple() from TypeScript.
    """
    final_message: AssistantMessage | None = None

    async for event in stream_simple(model, context, options):
        final_message = _extract_final(event) or final_message

    if final_message is None:
        raise RuntimeError("Stream completed without a final message")

    return final_message


async def stream(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """
    Stream with provider-specific options (no reasoning normalization).
    Mirrors stream() from TypeScript.
    """
    from openprogram.models.auth.provider_policy import require_supported_provider
    require_supported_provider(model.provider)
    with _job_operation_deadline() as record_activity:
        opts = options or StreamOptions()

        provider = get_api_provider(model.api)
        if provider is None:
            raise ValueError(f"No stream function registered for API: {model.api!r}")

        budget = BudgetedRequest.begin(model, context, opts, provider)
        receipt = None
        try:
            receipt = RequestReceipt.begin(model, opts, provider_context=context, provider=provider, budget=budget)
            if budget is not None:
                opts = budget.clamp(opts, model)
            if receipt is not None:
                opts = receipt.clamp(opts, model)

            # Auto-resolve API key from the AuthStore if not set (same as stream_simple)
            if not opts.api_key and getattr(provider, "requires_credentials", True):
                opts = opts.model_copy(
                    update={"api_key": resolve_provider_key(model.provider)},
                )
        except BaseException:
            if budget is not None:
                budget.release()
            if receipt is not None:
                receipt.release()
            raise

        async for event in _delivered(provider.stream, provider, model, context, opts, budget, receipt):
            record_activity("provider_data")
            yield event


async def complete(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessage:
    """
    Get a complete response with provider-specific options.
    Mirrors complete() from TypeScript.
    """
    final_message: AssistantMessage | None = None

    async for event in stream(model, context, options):
        final_message = _extract_final(event) or final_message

    if final_message is None:
        raise RuntimeError("Stream completed without a final message")

    return final_message


def _idle_bound(provider) -> float | None:
    """The idle bound this provider's stream is read under, or ``None`` when it keeps its own."""
    if bounds_own_stream_idle(provider):
        return None
    return event_idle_timeout_s()


async def _delivered(stream_fn, provider, model: Model, context, opts, budget, receipt):
    """Yield one request's events, re-sending it when its reply fails in transport.

    Each attempt is read under the idle bound and metered on its own. The
    usage of a reply is recorded at its terminal event, not after the loop:
    the consumer (agent_loop) returns the moment it sees the done/error
    event, leaving this generator suspended at ``yield``, and the terminal
    event carries the final message. An attempt that ends without one after
    reaching the provider is recorded too (``_record_failed``), since the
    provider may have billed it.

    A failed attempt whose error is transport is sent again within the
    shared retry bound (``resend.py``). The consumer sees one ``start``, then
    a ``retry`` event before each new attempt; ``restart=True`` on it voids
    the content the failed attempt streamed.

    Each attempt binds a request slot the adapter fills with the body it
    sends (``recording/capture.py``). The request is checked against its
    lane's previous one before it goes out, the check is settled with the
    reply's usage, and the usage event records it.
    """
    governed = budget is not None or receipt is not None
    resend = Resend(model, opts, governed=governed)
    idle_s = _idle_bound(provider)
    started = False
    while True:
        recorded = False
        shown = False
        partial = None
        slot = RequestSlot(on_capture=check_request)
        try:
            async for event in _metered(stream_fn, model, context, opts, budget, receipt,
                                        idle_s=idle_s, slot=slot):
                if not recorded:
                    final = _extract_final(event)
                    if final is not None:
                        recorded = True
                        prefix = settle_check(slot.result, final.usage)
                        if receipt is not None:
                            receipt.settle(final, prefix)
                        elif budget is not None:
                            budget.settle(model, final, opts, prefix=prefix)
                        else:
                            _record_usage(model, final, opts, prefix)
                        resend.delivered()
                    else:
                        partial = _fuller(partial, _partial_of(event))
                        if receipt is not None:
                            _observe_usage(receipt, event)
                kind = _event_kind(event)
                if kind == "start":
                    if started:
                        # One reply to the consumer: a second ``start`` would
                        # have it append a second partial message.
                        continue
                    started = True
                elif kind != "retry":
                    shown = True
                yield event
            return
        except Exception as exc:
            if recorded:
                raise
            if not governed and reached_provider(exc, answered=shown or _usage_seen(partial) > 0):
                _record_failed(model, partial, opts)
            _read_quota_after(model, exc)
            plan = resend.plan(exc, shown=shown)
            if plan is None:
                raise
            notice, wait_s = plan
            if not started:
                # A consumer shows a retry on the reply it is building.
                started = True
                yield EventStart(partial=AssistantMessage.empty(model))
            yield notice
            await resend.wait(wait_s)


def _event_kind(event) -> str | None:
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", None)


def _partial_of(event) -> AssistantMessage | None:
    partial = event.get("partial") if isinstance(event, dict) else getattr(event, "partial", None)
    return partial if isinstance(partial, AssistantMessage) else None


def _usage_seen(message: AssistantMessage | None) -> int:
    usage = getattr(message, "usage", None)
    return 0 if usage is None else usage.input + usage.output + usage.cache_read + usage.cache_write


def _fuller(held: AssistantMessage | None, partial: AssistantMessage | None) -> AssistantMessage | None:
    """The partial reporting the most usage so far; the later one on a tie."""
    if partial is None:
        return held
    return partial if _usage_seen(partial) >= _usage_seen(held) else held


def _read_quota_after(model: Model, exc: BaseException) -> None:
    """A 402, 429 or spent quota: read the provider's quota once, to show the new state."""
    try:
        from openprogram.models.usage.quota_fetchers import after_failure
        after_failure(getattr(model, "provider", "") or "", exc)
    except Exception:
        _log.debug("quota read after a failure not scheduled", exc_info=True)


def _record_failed(model: Model, partial, options) -> None:
    """Record a request that reached the provider and ended without a final message."""
    try:
        from openprogram.models.usage import record_failed_request
        record_failed_request(
            model, partial, session_id=getattr(options, "session_id", None) if options else None,
        )
    except Exception:
        _log.warning("failed request not recorded for %s", getattr(model, "id", model), exc_info=True)


async def _metered(stream_fn, model: Model, context, opts, budget, receipt=None, *,
                   idle_s: float | None = None, slot: RequestSlot | None = None):
    """Drive the provider stream, marking the reservation started around I/O.

    Failures while constructing the provider iterator release a reservation.
    Once ``start`` succeeds, any failure or cancellation conservatively keeps
    exposure held because the request may already have reached the provider.

    ``idle_s`` bounds the wait for each event (``utils/stream_idle.py``): a
    reply silent that long ends with ``StreamIdleTimeout``, a transport error
    the caller's retry policy re-sends, and the stalled provider stream is
    closed first.
    """
    try:
        # An opaque provider may perform I/O while constructing its iterator.
        # Once invoked, absence of a receipt cannot prove the request was free.
        if receipt is not None:
            receipt.start()
        context = request_repair.prepared(model, context)
        if slot is not None:
            bind_slot(slot)
        events = request_repair.repairing(stream_fn(model, context, opts), stream_fn, model, context, opts)
        if budget is not None and receipt is None:
            budget.start()
    except BaseException:
        if budget is not None:
            budget.release()
        if receipt is not None:
            receipt.release()
        raise

    first_token = FirstToken()
    try:
        async for event in idle_bounded(events, idle_s, label=f"{model.provider} stream"):
            first_token.see(event)
            first_token.stamp(event)
            yield event
    except BaseException:
        import asyncio
        from ..utils.event_stream import close_stream
        task = asyncio.current_task()
        stopping = bool(task and task.cancelling()) or bool(opts.signal and opts.signal.is_set())
        await close_stream(events, timeout=0.5 if stopping else None)
        raise


def _record_usage(model: Model, final, options, prefix=None) -> None:
    """Single metering chokepoint: every stream()/stream_simple() ends here,
    so every LLM call this module serves gets recorded. Best-effort — a
    metering failure must never surface to the caller. The call source
    (chat / compaction / memory / …) is read from the contextvar set by
    the caller's ``usage_scope(...)``; session_id comes off the options.
    """
    if final is None:
        return
    try:
        from openprogram.models.usage import record_message
        session_id = getattr(options, "session_id", None) if options else None
        record_message(model, final, session_id=session_id, prefix=prefix)
    except Exception:
        # Metering must never fail the turn, but the usage row is lost.
        _log.warning(
            "usage not recorded for %s (session %s)",
            getattr(model, "id", model),
            getattr(options, "session_id", None) if options else None,
            exc_info=True)


def _observe_usage(receipt, event):
    partial = event.get("partial") if isinstance(event, dict) else getattr(event, "partial", None)
    if isinstance(partial, dict):
        partial = AssistantMessage.model_validate(partial)
    if isinstance(partial, AssistantMessage):
        receipt.observe(partial)


def _extract_final(event) -> AssistantMessage | None:
    """
    Pull the AssistantMessage out of a terminal event.
    Provider implementations sometimes yield dicts rather than BaseModel
    instances — normalize both shapes.
    """
    etype = event["type"] if isinstance(event, dict) else getattr(event, "type", None)
    if etype == "done":
        payload = event["message"] if isinstance(event, dict) else event.message
    elif etype == "error":
        payload = event["error"] if isinstance(event, dict) else event.error
    else:
        return None

    if isinstance(payload, AssistantMessage):
        return _typed_blocks(payload)
    if isinstance(payload, dict):
        return AssistantMessage.model_validate(payload)
    return payload


#: What an adapter keeps on a block it is still filling, beside the block's own fields.
_WORKING_KEYS = ("index", "partial_json")
_BLOCK_TYPES = {"text": TextContent, "thinking": ThinkingContent}


def _typed_blocks(message: AssistantMessage) -> AssistantMessage:
    """``message`` with the text and thinking a reply ended inside as content blocks.

    An adapter fills a block as a plain dict and types it when the block
    ends. A reply cut off mid-block (a stop the adapter answers itself, a
    transport error after content) ends with that dict still in its final
    message, where every reader expects a content block and serializing it
    warns. The text is the same; only its type is settled here, in place,
    for the event's message and every consumer of it. An unfinished tool
    call is left as the adapter left it: it was never a call to dispatch.
    """
    for position, block in enumerate(message.content):
        if not isinstance(block, dict):
            continue
        block_type = _BLOCK_TYPES.get(block.get("type"))
        if block_type is None:
            continue
        fields = {key: value for key, value in block.items() if key not in _WORKING_KEYS}
        try:
            message.content[position] = block_type.model_validate(fields)
        except ValueError:
            _log.debug("unfinished %s block left as the adapter's dict", block.get("type"), exc_info=True)
    return message
