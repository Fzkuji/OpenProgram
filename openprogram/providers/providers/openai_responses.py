"""
OpenAI Responses API provider.

Handles reasoning, prompt caching, service tiers, and GitHub Copilot integration.

Mirrors openai-responses.ts
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import calculate_cost, supports_xhigh
from openprogram.providers.providers.github_copilot_headers import build_copilot_dynamic_headers, has_copilot_vision_input
from openprogram.providers.providers.openai_responses_shared import (
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from openprogram.providers.providers.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

_OPENAI_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-codex", "opencode"})


def stream_openai_responses(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from the OpenAI Responses API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        try:
            import openai
        except ImportError:
            raise ImportError("openai is required: pip install openai")

        from openprogram.providers.env_api_keys import get_env_api_key

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": model.api,
            "provider": model.provider,
            "model": model.id,
            "usage": {
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "total_tokens": 0,
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
            },
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }

        try:
            api_key = opts.get("api_key") or get_env_api_key(model.provider) or ""
            client = _create_client(model, context, api_key, opts.get("headers"))
            params = _build_params(model, context, opts)

            if opts.get("on_payload"):
                opts["on_payload"](params)

            openai_stream = client.responses.create(
                **params,
                stream=True,
            )
            ev_stream.push({"type": "start", "partial": output})

            await process_responses_stream(
                openai_stream,
                output,
                ev_stream,
                model,
                service_tier=opts.get("service_tier"),
                apply_service_tier_pricing=_apply_service_tier_pricing,
            )

            if output["stop_reason"] in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for b in output["content"]:
                if isinstance(b, dict):
                    b.pop("index", None)
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            ev_stream.push({"type": "error", "reason": "error", "error": output})
            ev_stream.end(output)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_openai_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for OpenAI Responses API streaming."""
    from openprogram.providers.env_api_keys import get_env_api_key

    api_key = (getattr(options, "api_key", None) if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)

    reasoning = getattr(options, "reasoning", None) if options else None
    reasoning_effort = supports_xhigh(model) and reasoning or clamp_reasoning(reasoning)

    return stream_openai_responses(model, context, {
        **base_dict,
        "reasoning_effort": reasoning_effort,
    })


def _create_client(
    model: "Model",
    context: "Context",
    api_key: str,
    options_headers: dict[str, str] | None = None,
) -> Any:
    import openai

    headers: dict[str, str] = {**(getattr(model, "headers", None) or {}), **(options_headers or {})}

    # GitHub Copilot needs dynamic headers
    messages = context.messages
    has_images = has_copilot_vision_input(messages)
    copilot_headers = build_copilot_dynamic_headers(messages, has_images)

    # Merge copilot headers if this looks like a copilot endpoint
    base_url = getattr(model, "base_url", None) or ""
    if "copilot" in base_url.lower() or "githubcopilot" in base_url.lower():
        headers.update(copilot_headers)

    kwargs: dict[str, Any] = {
        "api_key": api_key or "dummy",
        "default_headers": headers if headers else None,
    }
    if base_url:
        kwargs["base_url"] = base_url

    return openai.AsyncOpenAI(**{k: v for k, v in kwargs.items() if v is not None})


def _resolve_cache_retention(cache_retention: str | None) -> str:
    if cache_retention:
        return cache_retention
    if os.environ.get("PI_CACHE_RETENTION") == "long":
        return "long"
    return "short"


def _build_params(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
) -> dict[str, Any]:
    messages = convert_responses_messages(model, context, _OPENAI_TOOL_CALL_PROVIDERS)
    params: dict[str, Any] = {
        "model": model.id,
        "input": messages,
        "stream": True,
    }

    session_id = opts.get("session_id")
    if session_id:
        params["prompt_cache_key"] = session_id

    if opts.get("max_tokens"):
        params["max_output_tokens"] = opts["max_tokens"]
    if opts.get("temperature") is not None:
        params["temperature"] = opts["temperature"]

    tools = getattr(context, "tools", None)
    if tools:
        params["tools"] = convert_responses_tools(tools)

    service_tier = opts.get("service_tier")
    if service_tier:
        params["service_tier"] = service_tier

    params["store"] = False

    reasoning_effort = opts.get("reasoning_effort")
    reasoning_summary = opts.get("reasoning_summary")
    if getattr(model, "reasoning", False):
        if reasoning_effort or reasoning_summary:
            params["reasoning"] = {
                "effort": reasoning_effort or "medium",
                "summary": reasoning_summary or "auto",
            }
            params["include"] = ["reasoning.encrypted_content"]

    return params


def _apply_service_tier_pricing(usage: dict[str, Any], service_tier: str | None) -> None:
    """Apply service tier pricing adjustments (flex tier costs less for cached)."""
    if service_tier != "flex":
        return
    # Flex tier: cached tokens billed at lower rate (noop here since pricing is already handled)
    pass
