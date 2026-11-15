"""
Google Gemini CLI / Cloud Code Assist provider.

Uses the Cloud Code Assist API endpoint with OAuth authentication,
supports thinking/reasoning, and handles retries for rate limits.

Mirrors google-gemini-cli.ts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

from openprogram.models.providers.catalog.models import calculate_cost
from openprogram.models.providers.shared.google import (
    GOOGLE_SEARCH_TOOL,
    convert_messages,
    convert_tools,
    google_search_applies,
    grounding_citations,
    grounding_search_blocks,
    is_thinking_part,
    map_stop_reason_string,
    map_tool_choice,
    retain_thought_signature,
)
from openprogram.models.providers.shared.simple_options import build_base_options, clamp_reasoning
from openprogram.models.providers.shared.validate_modalities import validate_input_modalities
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.models.providers.utils.http_client import build_async_client
from openprogram.models.providers.utils.sanitize_unicode import sanitize_surrogates
from openprogram.models.providers.utils.stream_idle import (
    event_idle_timeout_s,
    idle_bounded,
    idle_bounded_open,
)
from openprogram.models.providers.utils.stream_retry import retry_after_beyond_cap
import logging

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openprogram.models.providers.types import Context, Model, SimpleStreamOptions

GoogleThinkingLevel = str  # "THINKING_LEVEL_UNSPECIFIED" | "MINIMAL" | "LOW" | "MEDIUM" | "HIGH"

_DEFAULT_ENDPOINT = "https://cloudcode-pa.googleapis.com"
_MAX_RETRIES = 3
_BASE_DELAY_MS = 1000
_MAX_EMPTY_STREAM_RETRIES = 2
_EMPTY_STREAM_BASE_DELAY_MS = 500

_GEMINI_CLI_HEADERS = {
    "User-Agent": "google-cloud-sdk vscode_cloudshelleditor/0.1",
    "X-Goog-Api-Client": "gl-node/22.17.0",
    "Client-Metadata": json.dumps({
        "ideType": "IDE_UNSPECIFIED",
        "platform": "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }),
}

_tool_call_counter = 0


def extract_retry_delay(error_text: str) -> int | None:
    """Extract retry delay in milliseconds from a Gemini error response."""
    patterns = [
        (r"retry[- ]?after[:\s]+(\d+)", 1000),
        (r"quota will reset after\s+(\d+)s", 1000),
        (r"please retry in\s+(\d+)ms", 1),
        (r"please retry in\s+(\d+)s", 1000),
        (r'"retryDelay":\s*"([\d.]+)s"', 1000),
    ]
    for pattern, multiplier in patterns:
        m = re.search(pattern, error_text, re.IGNORECASE)
        if m:
            delay_ms = int(float(m.group(1)) * multiplier)
            if delay_ms > 0:
                return delay_ms + 1000
    return None


async def _data_lines(response: Any):
    """The SSE ``data:`` lines of ``response``; blank and comment lines are not events."""
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            yield line


def _is_retryable_error(status: int, error_text: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    return bool(re.search(r"rate.?limit|overloaded|quota|resource.?exhausted", error_text, re.IGNORECASE))


def _resolve_endpoints(model: "Model") -> list[str]:
    model_base_url = getattr(model, "base_url", None)
    return [model_base_url] if model_base_url else [_DEFAULT_ENDPOINT]


def stream_google_gemini_cli(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from Google Gemini CLI / Cloud Code Assist API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    validate_input_modalities(model, context)

    async def _run() -> None:
        global _tool_call_counter
        try:
            import httpx  # noqa: F401 -- availability check before the request
        except ImportError:
            raise ImportError(
                "The HTTP client is missing; reinstall the complete OpenProgram release."
            )

        from openprogram.models.providers.settings.env_api_keys import resolve_provider_key

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": model.api,
            "provider": model.provider,
            "model": model.id,
            "usage": {
                "tokens_reported": False,
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "total_tokens": 0,
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
            },
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }

        try:
            api_key = opts.get("api_key") or resolve_provider_key(model.provider) or ""

            # Endpoint selection
            endpoints = _resolve_endpoints(model)

            headers: dict[str, str] = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **_GEMINI_CLI_HEADERS,
                **(getattr(model, "headers", None) or {}),
                **(opts.get("headers") or {}),
            }

            project_id = opts.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
            if not project_id:
                project_id = await _discover_project_id(headers, endpoints[0])
            contents = convert_messages(model, context)
            request_body = _build_request_body(model, context, opts, contents, project_id)

            if opts.get("on_payload"):
                opts["on_payload"](request_body)
            from ...recording.capture import GOOGLE, capture_request
            capture_request(model, request_body, wire=GOOGLE)

            ev_stream.push({"type": "start", "partial": output})

            idle_s = event_idle_timeout_s()
            from ...streaming.budget import provider_retry_attempts
            max_attempts = provider_retry_attempts(_MAX_RETRIES + 1)
            attempts_used = 0
            endpoint_index = 0
            while attempts_used < max_attempts:
                base_url = endpoints[min(endpoint_index, len(endpoints) - 1)]
                endpoint_url = f"{base_url.rstrip('/')}/v1internal:streamGenerateContent?alt=sse"
                # Hardened client: decoupled + generous timeouts (was a single
                # timeout=120.0 float that capped the streaming read at 120s and
                # false-positived over a proxy/VPN), TCP keepalive, force-IPv4,
                # proxy — all from the shared builder.
                from openprogram.host.security.url_policy import (
                    OwnerURLException,
                    normalize_origin,
                )

                configured_origin = normalize_origin(base_url)
                async with build_async_client(
                    consumer="provider.configured_api",
                    configured_origin=configured_origin,
                    owner_exception=OwnerURLException(
                        consumer="provider.configured_api",
                        origin=configured_origin,
                    ),
                ) as client:
                    attempts_used += 1
                    # Read under its own idle bound, not the stream
                    # chokepoint's: the waits between attempts below are
                    # silent by design and must not count as a stall.
                    async with contextlib.AsyncExitStack() as attempt_scope:
                        response = await idle_bounded_open(
                            attempt_scope.enter_async_context(client.stream(
                                "POST",
                                endpoint_url,
                                headers=headers,
                                content=json.dumps(request_body),
                            )),
                            idle_s,
                            label="gemini-subscription",
                        )
                        if response.status_code not in (200, 201):
                            error_text_bytes = await response.aread()
                            error_text = error_text_bytes.decode()
                            # Immediate endpoint cascade for auth/not-found errors
                            if (
                                response.status_code in (403, 404)
                                and endpoint_index < len(endpoints) - 1
                                and attempts_used < max_attempts
                            ):
                                endpoint_index += 1
                                continue
                            if attempts_used < max_attempts and _is_retryable_error(response.status_code, error_text):
                                hinted = extract_retry_delay(error_text)
                                # A quota reset hours away is not sat through
                                # in-process: the error goes up at once.
                                if hinted and retry_after_beyond_cap(hinted / 1000):
                                    raise RuntimeError(f"HTTP {response.status_code}: {error_text}")
                                delay = hinted or (
                                    _BASE_DELAY_MS * (2 ** (attempts_used - 1))
                                )
                                await asyncio.sleep(delay / 1000)
                                continue
                            raise RuntimeError(f"HTTP {response.status_code}: {error_text}")

                        current_block: dict[str, Any] | None = None
                        blocks = output["content"]
                        grounding: Any = None

                        def block_idx() -> int:
                            return len(blocks) - 1

                        _sig = opts.get("signal")
                        async for line in idle_bounded(
                            _data_lines(response), idle_s, label="gemini-subscription event",
                        ):
                            # Caller cancel (Stop button): raising unwinds
                            # through ``async with client.stream(...)``, which
                            # closes the connection.
                            if _sig is not None and callable(getattr(_sig, "is_set", None)) and _sig.is_set():
                                from openprogram.models.providers.utils.errors import StreamAborted
                                raise StreamAborted("stream cancelled by caller signal")
                            data_str = line[6:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            candidates = chunk.get("candidates", [])
                            for candidate in candidates:
                                if candidate.get("groundingMetadata"):
                                    grounding = candidate["groundingMetadata"]
                                content_obj = candidate.get("content", {})
                                parts = content_obj.get("parts", [])
                                for part in parts:
                                    text = part.get("text")
                                    thought = part.get("thought", False)
                                    thought_sig = part.get("thoughtSignature")
                                    func_call = part.get("functionCall")

                                    if text is not None:
                                        is_thinking = is_thinking_part({"thought": thought})
                                        if (
                                            not current_block
                                            or (is_thinking and current_block.get("type") != "thinking")
                                            or (not is_thinking and current_block.get("type") != "text")
                                        ):
                                            if current_block:
                                                if current_block.get("type") == "text":
                                                    ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                                                else:
                                                    ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})
                                            if is_thinking:
                                                current_block = {"type": "thinking", "thinking": "", "thinking_signature": None}
                                                output["content"].append(current_block)
                                                ev_stream.push({"type": "thinking_start", "content_index": block_idx(), "partial": output})
                                            else:
                                                current_block = {"type": "text", "text": ""}
                                                output["content"].append(current_block)
                                                ev_stream.push({"type": "text_start", "content_index": block_idx(), "partial": output})

                                        if current_block.get("type") == "thinking":
                                            current_block["thinking"] = current_block.get("thinking", "") + text
                                            current_block["thinking_signature"] = retain_thought_signature(
                                                current_block.get("thinking_signature"), thought_sig
                                            )
                                            ev_stream.push({"type": "thinking_delta", "content_index": block_idx(), "delta": text, "partial": output})
                                        else:
                                            current_block["text"] = current_block.get("text", "") + text
                                            current_block["text_signature"] = retain_thought_signature(
                                                current_block.get("text_signature"), thought_sig
                                            )
                                            ev_stream.push({"type": "text_delta", "content_index": block_idx(), "delta": text, "partial": output})

                                    if func_call:
                                        if current_block:
                                            if current_block.get("type") == "text":
                                                ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                                            else:
                                                ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})
                                            current_block = None

                                        provided_id = func_call.get("id")
                                        needs_new_id = (
                                            not provided_id
                                            or any(b.get("type") == "toolCall" and b.get("id") == provided_id for b in output["content"])
                                        )
                                        _tool_call_counter += 1
                                        tool_call_id = (
                                            f"{func_call.get('name', 'tool')}_{int(time.time() * 1000)}_{_tool_call_counter}"
                                            if needs_new_id
                                            else provided_id
                                        )
                                        tool_call: dict[str, Any] = {
                                            "type": "toolCall",
                                            "id": tool_call_id,
                                            "name": func_call.get("name", ""),
                                            "arguments": func_call.get("args", {}) or {},
                                        }
                                        if thought_sig:
                                            tool_call["thought_signature"] = thought_sig
                                        output["content"].append(tool_call)
                                        ev_stream.push({"type": "toolcall_start", "content_index": block_idx(), "partial": output})
                                        ev_stream.push({"type": "toolcall_delta", "content_index": block_idx(), "delta": json.dumps(tool_call["arguments"]), "partial": output})
                                        ev_stream.push({"type": "toolcall_end", "content_index": block_idx(), "tool_call": tool_call, "partial": output})

                                finish_reason = candidate.get("finishReason")
                                if finish_reason:
                                    output["stop_reason"] = map_stop_reason_string(finish_reason)
                                    if any(b.get("type") == "toolCall" for b in output["content"]):
                                        output["stop_reason"] = "toolUse"

                            usage_meta = chunk.get("usageMetadata", {})
                            if usage_meta:
                                output["usage"]["tokens_reported"] = all(usage_meta.get(key) is not None for key in ("promptTokenCount", "candidatesTokenCount"))
                                output["usage"]["input"] = usage_meta.get("promptTokenCount", 0) or 0
                                output["usage"]["output"] = (usage_meta.get("candidatesTokenCount", 0) or 0) + (usage_meta.get("thoughtsTokenCount", 0) or 0)
                                output["usage"]["cache_read"] = usage_meta.get("cachedContentTokenCount", 0) or 0
                                output["usage"]["input"] = max(0, output["usage"]["input"] - output["usage"]["cache_read"])
                                output["usage"]["total_tokens"] = usage_meta.get("totalTokenCount", 0) or 0
                                calculate_cost(model, output["usage"])

                        # Citations land on the text before its text_end.
                        _apply_grounding_citations(output, grounding)

                        if current_block:
                            if current_block.get("type") == "text":
                                ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                            else:
                                ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})

                        _push_grounding_searches(output, ev_stream, grounding)

                        break  # Success, exit retry loop

            if output["stop_reason"] in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for b in output["content"]:
                if isinstance(b, dict):
                    b.pop("index", None)
            # User cancel — finalize as "aborted" (anthropic's cancel
            # semantics), preserving whatever content already streamed.
            _sig = opts.get("signal")
            if _sig is not None and callable(getattr(_sig, "is_set", None)) and _sig.is_set():
                output["stop_reason"] = "aborted"
                output["error_message"] = str(exc)
                ev_stream.push({"type": "error", "reason": "aborted", "error": output})
                ev_stream.end(output)
                return
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            # ev_stream.fail() — see openai-codex / openai_responses for the
            # rationale: push-error + end looks like a clean stream end, so
            # the consumer treats the failed stream as successful and
            # auto-retries it.
            ev_stream.fail(exc)

    # Owned by the stream: closing it (a steer, a stop, the idle bound)
    # cancels the request instead of leaving it to wait on the socket.
    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


def stream_simple_google_gemini_cli(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for Gemini CLI streaming."""
    base = build_base_options(model, options)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None

    if not reasoning:
        return stream_google_gemini_cli(model, context, {**base_dict, "thinking": {"enabled": False}})

    effort = clamp_reasoning(reasoning) or "low"

    if _is_gemini3_pro(model) or _is_gemini3_flash(model):
        return stream_google_gemini_cli(model, context, {
            **base_dict,
            "thinking": {"enabled": True, "level": _get_gemini3_thinking_level(effort, model)},
        })

    return stream_google_gemini_cli(model, context, {
        **base_dict,
        "thinking": {
            "enabled": True,
            "budget_tokens": _get_google_budget(model, effort, getattr(options, "thinking_budgets", None)),
        },
    })


def _apply_grounding_citations(output: dict[str, Any], grounding: Any) -> None:
    """Put Google Search grounding citations on the reply's last text block."""
    if not grounding:
        return
    text_blocks = [b for b in output["content"] if b.get("type") == "text"]
    if not text_blocks:
        return
    target = text_blocks[-1]
    # groundingSupports offsets cover the whole response text.
    citations = grounding_citations(grounding, "".join(b.get("text", "") for b in text_blocks))
    if citations:
        target["citations"] = citations


def _push_grounding_searches(output: dict[str, Any], ev_stream: Any, grounding: Any) -> None:
    """Append one ServerToolUse per grounding query; never a toolCall."""
    existing = sum(1 for b in output["content"] if b.get("type") == "serverToolUse")
    for block in grounding_search_blocks(grounding, first_id=existing):
        output["content"].append(block)
        index = len(output["content"]) - 1
        ev_stream.push({"type": "servertool_start", "content_index": index, "partial": output})
        ev_stream.push({"type": "servertool_end", "content_index": index, "server_tool": dict(block), "partial": output})


def _build_request_body(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
    contents: list[dict[str, Any]],
    project_id: str,
) -> dict[str, Any]:
    """Build CloudCodeAssistRequest body matching TS format."""
    # Inner request (generateContent params)
    request: dict[str, Any] = {"contents": contents}

    # System instruction
    if context.system_prompt:
        request["systemInstruction"] = {
            "parts": [{"text": sanitize_surrogates(context.system_prompt)}]
        }

    # Session ID
    if opts.get("session_id"):
        request["sessionId"] = opts["session_id"]

    # Generation config
    config: dict[str, Any] = {}
    if opts.get("max_tokens") is not None:
        config["maxOutputTokens"] = opts["max_tokens"]
    if opts.get("temperature") is not None:
        config["temperature"] = opts["temperature"]

    # Tools
    tools_list = getattr(context, "tools", None) or []
    if tools_list:
        use_parameters = any(m in model.id.lower() for m in ("claude-", "gpt-oss-"))
        converted = convert_tools(tools_list, use_parameters=use_parameters)
        if converted:
            request["tools"] = converted
        if opts.get("tool_choice"):
            request["toolConfig"] = {
                "functionCallingConfig": {"mode": map_tool_choice(opts["tool_choice"])}
            }
    # Google Search grounding. Function tools win when both are present
    # (see google_search_applies): the combination is rejected there.
    if opts.get("web_search") and google_search_applies(bool(request.get("tools"))):
        request["tools"] = [*(request.get("tools") or []), dict(GOOGLE_SEARCH_TOOL)]

    # Thinking config
    thinking_opts = opts.get("thinking")
    if thinking_opts and thinking_opts.get("enabled") and getattr(model, "reasoning", False):
        thinking_config: dict[str, Any] = {"includeThoughts": True}
        if thinking_opts.get("level") is not None:
            thinking_config["thinkingLevel"] = thinking_opts["level"]
        elif thinking_opts.get("budget_tokens") is not None:
            thinking_config["thinkingBudget"] = thinking_opts["budget_tokens"]
        config["thinkingConfig"] = thinking_config

    if config:
        request["generationConfig"] = config

    # Wrap in CloudCodeAssistRequest
    import random
    request_id = f"agent-{int(time.time() * 1000)}-{random.randint(100000, 999999)}"
    body: dict[str, Any] = {
        "project": project_id,
        "model": model.id,
        "request": request,
        "userAgent": "pi-coding-agent",
        "requestId": request_id,
    }

    return body


def _is_gemini3_pro(model: "Model") -> bool:
    """Matches gemini-3-pro-* and gemini-3.1-pro-* patterns."""
    return bool(re.search(r"gemini-3(?:\.1)?-pro", model.id.lower()))


def _is_gemini3_flash(model: "Model") -> bool:
    """Matches gemini-3-flash-* and gemini-3.1-flash-* patterns."""
    return bool(re.search(r"gemini-3(?:\.1)?-flash", model.id.lower()))


def _get_gemini3_thinking_level(effort: str, model: "Model") -> str:
    if _is_gemini3_pro(model):
        return "LOW" if effort in ("minimal", "low") else "HIGH"
    return {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}.get(effort, "LOW")


def _get_google_budget(model: "Model", effort: str, custom_budgets: dict[str, int] | None = None) -> int:
    if custom_budgets and effort in custom_budgets:
        return custom_budgets[effort]
    if "2.5-pro" in model.id:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}.get(effort, 2048)
    if "2.5-flash" in model.id:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}.get(effort, 2048)
    return -1


_cached_project_id: str = ""


async def _discover_project_id(headers: dict[str, str], base_url: str) -> str:
    """Call loadCodeAssist to discover the cloudaicompanionProject for this OAuth token.

    Result is cached in-process so the extra round-trip only happens once per session.
    """
    global _cached_project_id
    if _cached_project_id:
        return _cached_project_id
    try:
        from openprogram.host.security.safe_http import configured_safe_async_client
        from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

        url = f"{base_url.rstrip('/')}/v1internal:loadCodeAssist"
        origin = normalize_origin(base_url)
        body = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        async with configured_safe_async_client(
            "provider.configured_api",
            origin,
            owner_exception=OwnerURLException(
                consumer="provider.configured_api", origin=origin
            ),
        ) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(body))
            if resp.status_code == 200:
                data = resp.json()
                project = data.get("cloudaicompanionProject") or ""
                if project:
                    _cached_project_id = project
                    return project
    except Exception:
        # No discovered project: the caller proceeds without one.
        _log.debug("loadCodeAssist project discovery failed", exc_info=True)
    return ""
