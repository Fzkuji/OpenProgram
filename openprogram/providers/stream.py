"""
Unified streaming functions — mirrors packages/ai/src/stream.ts

Provides stream(), complete(), stream_simple(), complete_simple().
"""
from __future__ import annotations

from typing import AsyncGenerator

from .api_registry import get_api_provider
from .env_api_keys import get_env_api_key
from .providers import register_builtins
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

register_builtins()


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
    opts = options or SimpleStreamOptions()

    # Auto-resolve API key from env if not set
    if not opts.api_key:
        opts = opts.model_copy(update={"api_key": get_env_api_key(model.provider)})

    provider = get_api_provider(model.api)
    if provider is None:
        raise ValueError(f"No stream function registered for API: {model.api!r}")

    async for event in provider.stream_simple(model, context, opts):
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
        if isinstance(event, EventDone):
            final_message = event.message
        elif isinstance(event, EventError):
            final_message = event.error

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
    opts = options or StreamOptions()

    # Auto-resolve API key from env if not set (same behavior as stream_simple)
    if not opts.api_key:
        opts = opts.model_copy(update={"api_key": get_env_api_key(model.provider)})

    provider = get_api_provider(model.api)
    if provider is None:
        raise ValueError(f"No stream function registered for API: {model.api!r}")

    async for event in provider.stream(model, context, opts):
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
        if isinstance(event, EventDone):
            final_message = event.message
        elif isinstance(event, EventError):
            final_message = event.error

    if final_message is None:
        raise RuntimeError("Stream completed without a final message")

    return final_message
