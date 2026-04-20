"""
OpenAI Codex Responses API provider (ChatGPT backend).

Supports SSE transport with retry logic and session-based connection pooling.

Mirrors openai-codex-responses.ts
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import supports_xhigh
from openprogram.providers.providers.openai_responses_shared import (
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from openprogram.providers.providers.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
_MAX_RETRIES = 3
_BASE_DELAY_MS = 1000
_CODEX_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-codex", "opencode"})


def _is_retryable_error(status: int, error_text: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    import re
    return bool(re.search(r"rate.?limit|overloaded|service.?unavailable|upstream.?connect|connection.?refused", error_text, re.IGNORECASE))


def stream_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from OpenAI Codex (ChatGPT backend) Responses API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required: pip install httpx")

        from openprogram.providers.env_api_keys import get_env_api_key

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": "openai-codex-responses",
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
            base_url = getattr(model, "base_url", None) or _DEFAULT_CODEX_BASE_URL
            messages = convert_responses_messages(model, context, _CODEX_TOOL_CALL_PROVIDERS, include_system_prompt=False)
            request_body = _build_request_body(model, context, opts, messages)

            if opts.get("on_payload"):
                opts["on_payload"](request_body)

            headers: dict[str, str] = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                **(getattr(model, "headers", None) or {}),
                **(opts.get("headers") or {}),
            }

            ev_stream.push({"type": "start", "partial": output})

            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/codex/responses",
                    headers=headers,
                    content=json.dumps(request_body),
                ) as response:
                    if response.status_code not in (200, 201):
                        error_text = await response.aread()
                        raise RuntimeError(f"HTTP {response.status_code}: {error_text.decode()}")

                    sse_events = _parse_sse_stream(response)
                    await process_responses_stream(sse_events, output, ev_stream, model)

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


def stream_simple_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for OpenAI Codex Responses streaming."""
    from openprogram.providers.env_api_keys import get_env_api_key
    api_key = (getattr(options, "api_key", None) if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None
    reasoning_effort = reasoning if supports_xhigh(model) else clamp_reasoning(reasoning)

    return stream_openai_codex_responses(model, context, {**base_dict, "reasoning_effort": reasoning_effort})


def _build_request_body(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model.id,
        "input": messages,
        "stream": True,
        "store": False,
    }
    if context.system_prompt:
        body["instructions"] = context.system_prompt
    if opts.get("session_id"):
        body["prompt_cache_key"] = opts["session_id"]
    if opts.get("max_tokens"):
        body["max_output_tokens"] = opts["max_tokens"]
    if opts.get("temperature") is not None:
        body["temperature"] = opts["temperature"]

    tools = getattr(context, "tools", None)
    if tools:
        body["tools"] = convert_responses_tools(tools)
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = True

    reasoning_effort = opts.get("reasoning_effort")
    reasoning_summary = opts.get("reasoning_summary")
    if getattr(model, "reasoning", False) and reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
        if reasoning_summary:
            body["reasoning"]["summary"] = reasoning_summary
        body["include"] = ["reasoning.encrypted_content"]

    if opts.get("text_verbosity"):
        body["text"] = {"verbosity": opts["text_verbosity"]}

    return body


async def _parse_sse_stream(response: Any):
    """Parse SSE events from an httpx streaming response."""
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                pass
        elif line.startswith("event: "):
            pass  # Event type prefix, ignore
