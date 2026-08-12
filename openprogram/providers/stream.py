"""
Unified streaming functions — mirrors packages/ai/src/stream.ts

Provides stream(), complete(), stream_simple(), complete_simple().
"""
from __future__ import annotations

from contextlib import contextmanager
import time
from typing import AsyncGenerator

from .api_registry import get_api_provider
from .budget import BudgetedRequest
from .env_api_keys import resolve_provider_key
from .types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    EventDone,
    EventError,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)


@contextmanager
def _task_operation_deadline():
    """Bind one provider request to the claimed task's remaining time."""
    from openprogram.agent.task.runner import (
        current_task_operation_timeout,
        record_current_task_activity,
    )
    from openprogram.providers.utils.deadline import (
        get_deadline,
        reset_deadline,
        set_deadline,
    )

    timeout = current_task_operation_timeout(None, preemptibility="async")
    deadline = None if timeout is None else time.monotonic() + timeout
    outer = get_deadline()
    if outer is not None and (deadline is None or outer < deadline):
        deadline = outer
    token = set_deadline(deadline) if deadline is not None else None
    record_current_task_activity("operation_start")
    try:
        yield record_current_task_activity
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
    with _task_operation_deadline() as record_activity:
        provider = get_api_provider(model.api)
        async for event in stream_simple_with_provider(provider, model, context, options):
            record_activity("provider_data")
            yield event


async def stream_simple_with_provider(
    provider,
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """Stream through an already-resolved request-scoped provider snapshot."""
    opts = options or SimpleStreamOptions()

    if provider is None:
        raise ValueError(f"No stream function registered for API: {model.api!r}")

    # Reserve budget BEFORE credentials or network: a denied call must never
    # resolve a key or open a socket. Returns None for unbudgeted callers.
    budget = BudgetedRequest.begin(model, context, opts, provider)
    try:
        if budget is not None:
            opts = budget.clamp(opts, model)

        # Auto-resolve API key if not set. resolve_provider_key reads the
        # AuthStore (the single key source — no env vars, no config.json).
        if not opts.api_key and getattr(provider, "requires_credentials", True):
            opts = opts.model_copy(
                update={"api_key": resolve_provider_key(model.provider)},
            )
    except BaseException:
        if budget is not None:
            budget.release()
        raise

    # NOTE: the claude-code Meridian-profile header (x-meridian-profile) is
    # injected one layer down, in openai_completions.stream_simple — that's
    # the single chokepoint EVERY claude-code request passes through. This
    # wrapper is bypassed by some callers (e.g. providers/default_llm.py calls
    # the raw api-provider directly), so injecting here would miss them.
    # See docs/design/claude-code-meridian-profile.md.

    recorded = False
    async for event in _metered(provider.stream_simple, model, context, opts, budget):
        # Record AT the terminal event, not after the loop: the consumer
        # (agent_loop) returns the moment it sees the done/error event,
        # leaving this generator suspended at ``yield`` — a post-loop line
        # would never run. The terminal event carries the final message, so
        # we have everything we need to record before yielding it onward.
        if not recorded:
            final = _extract_final(event)
            if final is not None:
                recorded = True
                if budget is not None:
                    budget.settle(model, final, opts)
                else:
                    _record_usage(model, final, opts)
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
    with _task_operation_deadline() as record_activity:
        opts = options or StreamOptions()

        provider = get_api_provider(model.api)
        if provider is None:
            raise ValueError(f"No stream function registered for API: {model.api!r}")

        budget = BudgetedRequest.begin(model, context, opts, provider)
        try:
            if budget is not None:
                opts = budget.clamp(opts, model)

            # Auto-resolve API key from the AuthStore if not set (same as stream_simple)
            if not opts.api_key and getattr(provider, "requires_credentials", True):
                opts = opts.model_copy(
                    update={"api_key": resolve_provider_key(model.provider)},
                )
        except BaseException:
            if budget is not None:
                budget.release()
            raise

        recorded = False
        async for event in _metered(provider.stream, model, context, opts, budget):
            record_activity("provider_data")
            if not recorded:
                final = _extract_final(event)
                if final is not None:
                    recorded = True
                    if budget is not None:
                        budget.settle(model, final, opts)
                    else:
                        _record_usage(model, final, opts)
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


async def _metered(stream_fn, model: Model, context, opts, budget):
    """Drive the provider stream, marking the reservation started around I/O.

    Failures while constructing the provider iterator release a reservation.
    Once ``start`` succeeds, any failure or cancellation conservatively keeps
    exposure held because the request may already have reached the provider.
    """
    if budget is None:
        async for event in stream_fn(model, context, opts):
            yield event
        return

    try:
        events = stream_fn(model, context, opts)
        budget.start()
    except BaseException:
        budget.release()
        raise

    async for event in events:
        yield event


def _record_usage(model: Model, final, options) -> None:
    """Single metering chokepoint: every stream()/stream_simple() ends here,
    so every LLM call this module serves gets recorded. Best-effort — a
    metering failure must never surface to the caller. The call source
    (chat / compaction / memory / …) is read from the contextvar set by
    the caller's ``usage_scope(...)``; session_id comes off the options.
    """
    if final is None:
        return
    try:
        from openprogram.usage import record_message
        session_id = getattr(options, "session_id", None) if options else None
        record_message(model, final, session_id=session_id)
    except Exception:
        pass


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
        return payload
    if isinstance(payload, dict):
        return AssistantMessage.model_validate(payload)
    return payload
