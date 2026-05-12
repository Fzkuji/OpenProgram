"""
Google Gemini CLI / Cloud Code Assist provider.

Uses the Cloud Code Assist API endpoint with OAuth authentication,
supports thinking/reasoning, and handles retries for rate limits.

Mirrors google-gemini-cli.ts
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import calculate_cost
from openprogram.providers._shared.google import (
    convert_messages,
    convert_tools,
    is_thinking_part,
    map_stop_reason_string,
    map_tool_choice,
    retain_thought_signature,
)
from openprogram.providers._shared.simple_options import build_base_options, clamp_reasoning
from openprogram.providers._shared.validate_modalities import validate_input_modalities
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions, ThinkingLevel

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


def _is_retryable_error(status: int, error_text: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    return bool(re.search(r"rate.?limit|overloaded|quota|resource.?exhausted", error_text, re.IGNORECASE))


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
            import httpx
        except ImportError:
            raise ImportError("httpx is required: pip install httpx")

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

            # Endpoint selection
            model_base_url = getattr(model, "base_url", None)
            endpoints = [model_base_url] if model_base_url else [_DEFAULT_ENDPOINT]

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

            ev_stream.push({"type": "start", "partial": output})

            retry_count = 0
            endpoint_index = 0
            while retry_count <= _MAX_RETRIES:
                base_url = endpoints[min(endpoint_index, len(endpoints) - 1)]
                endpoint_url = f"{base_url.rstrip('/')}/v1internal:streamGenerateContent?alt=sse"
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        endpoint_url,
                        headers=headers,
                        content=json.dumps(request_body),
                    ) as response:
                        if response.status_code not in (200, 201):
                            error_text_bytes = await response.aread()
                            error_text = error_text_bytes.decode()
                            # Immediate endpoint cascade for auth/not-found errors
                            if response.status_code in (403, 404) and endpoint_index < len(endpoints) - 1:
                                endpoint_index += 1
                                continue
                            if retry_count < _MAX_RETRIES and _is_retryable_error(response.status_code, error_text):
                                delay = extract_retry_delay(error_text) or (_BASE_DELAY_MS * (2 ** retry_count))
                                await asyncio.sleep(delay / 1000)
                                retry_count += 1
                                continue
                            raise RuntimeError(f"HTTP {response.status_code}: {error_text}")

                        current_block: dict[str, Any] | None = None
                        blocks = output["content"]

                        def block_idx() -> int:
                            return len(blocks) - 1

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            candidates = chunk.get("candidates", [])
                            for candidate in candidates:
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
                                output["usage"]["input"] = usage_meta.get("promptTokenCount", 0) or 0
                                output["usage"]["output"] = (usage_meta.get("candidatesTokenCount", 0) or 0) + (usage_meta.get("thoughtsTokenCount", 0) or 0)
                                output["usage"]["cache_read"] = usage_meta.get("cachedContentTokenCount", 0) or 0
                                output["usage"]["total_tokens"] = usage_meta.get("totalTokenCount", 0) or 0
                                calculate_cost(model, output["usage"])

                        if current_block:
                            if current_block.get("type") == "text":
                                ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                            else:
                                ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})

                        break  # Success, exit retry loop

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
        import httpx
        url = f"{base_url.rstrip('/')}/v1internal:loadCodeAssist"
        body = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(body))
            if resp.status_code == 200:
                data = resp.json()
                project = data.get("cloudaicompanionProject") or ""
                if project:
                    _cached_project_id = project
                    return project
    except Exception:
        pass
    return ""
