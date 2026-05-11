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
from openprogram.providers._shared.openai_responses import (
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from openprogram.providers._shared.validate_modalities import validate_input_modalities
from openprogram.providers._shared.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
_MAX_RETRIES = 3
_BASE_DELAY_MS = 1000
_CODEX_TOOL_CALL_PROVIDERS = frozenset({"openai", "chatgpt-subscription", "opencode"})


def _is_retryable_error(status: int, error_text: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    import re
    return bool(re.search(r"rate.?limit|overloaded|service.?unavailable|upstream.?connect|connection.?refused", error_text, re.IGNORECASE))


def _resolve_codex_bearer_token(opts_api_key: str | None) -> str:
    """Resolve the bearer token codex requests need to authorize.

    Codex (ChatGPT subscription) auth has three valid sources:

      1. Explicit ``api_key`` passed in opts — caller-supplied,
         always wins.
      2. The provider's env var (handled by ``get_env_api_key``) —
         covers users on a bare API key with no OAuth flow.
      3. AuthManager's OAuth credential pool — covers users who ran
         ``codex login`` (or the OpenProgram OAuth wizard) so the
         pool has an ``OAuthPayload.access_token`` ready.

    Returns ``""`` when nothing yields a usable token, so the caller
    can raise the same "No API key for provider" error as before.
    Pre-fix: only sources 1 and 2 were checked, so OAuth users —
    despite a populated ``~/.openprogram/auth/openai-codex/default.json``
    — got the error the moment a stream started.
    """
    if opts_api_key:
        return opts_api_key

    from openprogram.providers.env_api_keys import get_env_api_key
    env_key = get_env_api_key("chatgpt-subscription")
    if env_key:
        return env_key

    # Try the OAuth pool. acquire_sync auto-refreshes if the token is
    # within the skew window, so we never serve a stale access_token.
    try:
        from openprogram.auth.manager import get_manager
        cred = get_manager().acquire_sync("chatgpt-subscription")
        payload = getattr(cred, "payload", None)
        token = getattr(payload, "access_token", None)
        if token:
            return token
    except Exception:
        # AuthManager raises when no provider config is registered or
        # no credentials exist. Both are recoverable — fall through to
        # the empty-string return so the caller's check fires the same
        # actionable error message.
        pass

    return ""


def stream_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from OpenAI Codex (ChatGPT backend) Responses API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    validate_input_modalities(model, context)

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required: pip install httpx")

        from openprogram.providers.types import AssistantMessage, Usage

        output = AssistantMessage(
            content=[],
            api="chatgpt-subscription",
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="stop",
            timestamp=int(time.time() * 1000),
        )

        try:
            api_key = _resolve_codex_bearer_token(opts.get("api_key"))
            if not api_key:
                raise ValueError(f"No API key for provider: {model.provider}")
            base_url = getattr(model, "base_url", None) or _DEFAULT_CODEX_BASE_URL
            messages = convert_responses_messages(model, context, _CODEX_TOOL_CALL_PROVIDERS, include_system_prompt=False)
            request_body = _build_request_body(model, context, opts, messages)

            if opts.get("on_payload"):
                opts["on_payload"](request_body)

            try:
                _cache_key = request_body.get("prompt_cache_key", "")
                _instr_len = len(request_body.get("instructions") or "")
                _tool_names = sorted(t.get("name", "") for t in (request_body.get("tools") or []))
                _reasoning = request_body.get("reasoning")
                _input_items = request_body.get("input") or []
                _input_text_len = sum(
                    len(c.get("text", ""))
                    for item in _input_items
                    if isinstance(item, dict)
                    for c in (item.get("content") or [])
                    if isinstance(c, dict) and isinstance(c.get("text"), str)
                )
                print(
                    f"[codex req] key={_cache_key!r} items={len(_input_items)} "
                    f"text_chars={_input_text_len} instr={_instr_len} "
                    f"tools={_tool_names} reasoning={_reasoning}",
                    flush=True,
                )
            except Exception:
                pass

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

            if output.stop_reason in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output.stop_reason, "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for b in output.content:
                if isinstance(b, dict):
                    b.pop("index", None)
            output.stop_reason = "error"
            output.error_message = str(exc)
            # Use ev_stream.fail() so the consumer's `async for`
            # raises this exception instead of seeing a normal
            # stream end. The previous push-error + end pattern
            # left the stream looking "successful" to agent_loop,
            # which then auto-retried — that's why a single SSE
            # idle timeout sent the worker into a busy-loop with
            # no progress in SessionDB.
            ev_stream.fail(exc)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for OpenAI Codex Responses streaming."""
    explicit_key = getattr(options, "api_key", None) if options else None
    api_key = _resolve_codex_bearer_token(explicit_key)
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
    # Codex backend rejects requests without `instructions` (HTTP 400), so
    # fall back to a minimal default when no system prompt was supplied.
    body["instructions"] = context.system_prompt or "You are a helpful assistant."
    if opts.get("session_id"):
        body["prompt_cache_key"] = opts["session_id"]
    # `max_output_tokens` and `temperature` are rejected by the Codex backend;
    # they're only valid on the public OpenAI Responses API.

    tools = getattr(context, "tools", None)
    if tools:
        body["tools"] = convert_responses_tools(tools)
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = True

    reasoning_effort = opts.get("reasoning_effort")
    reasoning_summary = opts.get("reasoning_summary")
    if getattr(model, "reasoning", False) and reasoning_effort:
        body["reasoning"] = {
            "effort": reasoning_effort,
            # Default to "auto" so the API streams a readable summary of the
            # reasoning trace. Without a summary field, Codex only returns
            # encrypted_content (opaque to the UI) and no thinking deltas ever
            # fire. Callers can override by passing reasoning_summary.
            "summary": reasoning_summary or "auto",
        }
        body["include"] = ["reasoning.encrypted_content"]

    if opts.get("text_verbosity"):
        body["text"] = {"verbosity": opts["text_verbosity"]}

    return body


SSE_IDLE_TIMEOUT_S = 300.0   # 5 min — match codex CLI default
SSE_TOTAL_TIMEOUT_S = 1800.0 # 30 min absolute ceiling


class StreamIdleTimeout(Exception):
    """No real data event received for SSE_IDLE_TIMEOUT_S."""


class StreamTotalTimeout(Exception):
    """Single SSE stream exceeded SSE_TOTAL_TIMEOUT_S."""


async def _parse_sse_stream(response: Any):
    """Parse SSE events from an httpx streaming response.

    OpenAI's Codex Responses API emits keepalive frames (event: ping
    / blank lines) every few seconds during reasoning. httpx's read
    timeout treats *any* incoming bytes as activity and never trips
    on a stalled stream that's still echoing pings, so a session can
    hang forever waiting for content that never arrives.

    We track ``last_data_at`` independently — only "real" data events
    (i.e. parsed JSON payloads other than [DONE]) refresh it. If
    nothing of substance arrives for SSE_IDLE_TIMEOUT_S, we raise.
    A separate hard ceiling (SSE_TOTAL_TIMEOUT_S) backstops genuinely
    stuck requests that never even hit idle (e.g. ping-flooded).
    """
    import asyncio
    import time as _time
    deadline = _time.monotonic() + SSE_TOTAL_TIMEOUT_S
    last_data_at = _time.monotonic()
    line_iter = response.aiter_lines().__aiter__()
    while True:
        now = _time.monotonic()
        if now >= deadline:
            raise StreamTotalTimeout(
                f"SSE total budget {SSE_TOTAL_TIMEOUT_S}s exceeded")
        idle_left = SSE_IDLE_TIMEOUT_S - (now - last_data_at)
        if idle_left <= 0:
            raise StreamIdleTimeout(
                f"no SSE data event for {SSE_IDLE_TIMEOUT_S}s")
        # Wait for the next raw line. We bound by whichever limit
        # fires sooner so we never block past the idle threshold.
        wait = min(idle_left, deadline - now)
        try:
            line = await asyncio.wait_for(line_iter.__anext__(), timeout=wait)
        except asyncio.TimeoutError:
            # No bytes at all — that's both no-line and no-data.
            raise StreamIdleTimeout(
                f"no SSE bytes for {SSE_IDLE_TIMEOUT_S}s")
        except StopAsyncIteration:
            return
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            # Only real, parsed data events refresh the idle timer —
            # keepalive pings never arrive here, so they can't stall
            # the abort path.
            last_data_at = _time.monotonic()
            yield evt
        elif line.startswith("event: "):
            pass  # Event type prefix; not enough to count as data.
