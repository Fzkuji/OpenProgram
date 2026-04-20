"""
Google Vertex AI provider.

Streams responses from Vertex AI models with thinking/reasoning and
tool calling support.

Mirrors google-vertex.ts
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import calculate_cost
from openprogram.providers.providers.google_shared import (
    convert_messages,
    convert_tools,
    is_thinking_part,
    map_stop_reason,
    map_tool_choice,
    retain_thought_signature,
)
from openprogram.providers.providers.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.providers.types import AssistantMessage, Context, Model, SimpleStreamOptions, ThinkingLevel

_tool_call_counter = 0


def stream_google_vertex(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream responses from Google Vertex AI."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        global _tool_call_counter
        try:
            from google.genai import Client as GoogleGenAI  # type: ignore[import]
        except ImportError:
            try:
                from google import genai  # type: ignore[import]
                GoogleGenAI = genai.Client
            except ImportError:
                raise ImportError("google-genai is required for Vertex provider: pip install google-genai")

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": "google-vertex",
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
            project = _resolve_project(opts)
            location = _resolve_location(opts)
            client = _create_client(model, project, location, opts.get("headers"))
            params = _build_params(model, context, opts)

            if opts.get("on_payload"):
                opts["on_payload"](params)

            ev_stream.push({"type": "start", "partial": output})

            current_block: dict[str, Any] | None = None
            blocks = output["content"]

            def block_idx() -> int:
                return len(blocks) - 1

            google_stream = client.models.generate_content_stream(**params)
            for chunk in google_stream:
                candidates = getattr(chunk, "candidates", None) or []
                for candidate in candidates:
                    content_obj = getattr(candidate, "content", None)
                    parts = getattr(content_obj, "parts", None) or []
                    for part in parts:
                        part_dict = part if isinstance(part, dict) else part.__dict__
                        text = part_dict.get("text")
                        thought = part_dict.get("thought", False)
                        thought_sig = part_dict.get("thoughtSignature")
                        func_call = part_dict.get("functionCall")

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

                            fc_dict = func_call if isinstance(func_call, dict) else func_call.__dict__
                            provided_id = fc_dict.get("id")
                            needs_new_id = (
                                not provided_id
                                or any(b.get("type") == "toolCall" and b.get("id") == provided_id for b in output["content"])
                            )
                            _tool_call_counter += 1
                            tool_call_id = (
                                f"{fc_dict.get('name', 'tool')}_{int(time.time() * 1000)}_{_tool_call_counter}"
                                if needs_new_id
                                else provided_id
                            )
                            tool_call: dict[str, Any] = {
                                "type": "toolCall",
                                "id": tool_call_id,
                                "name": fc_dict.get("name", ""),
                                "arguments": fc_dict.get("args", {}) or {},
                            }
                            if thought_sig:
                                tool_call["thought_signature"] = thought_sig
                            output["content"].append(tool_call)
                            ev_stream.push({"type": "toolcall_start", "content_index": block_idx(), "partial": output})
                            import json
                            ev_stream.push({"type": "toolcall_delta", "content_index": block_idx(), "delta": json.dumps(tool_call["arguments"]), "partial": output})
                            ev_stream.push({"type": "toolcall_end", "content_index": block_idx(), "tool_call": tool_call, "partial": output})

                    finish_reason = getattr(candidate, "finish_reason", None)
                    if finish_reason:
                        output["stop_reason"] = map_stop_reason(finish_reason)
                        if any(b.get("type") == "toolCall" for b in output["content"]):
                            output["stop_reason"] = "toolUse"

                usage_meta = getattr(chunk, "usage_metadata", None)
                if usage_meta:
                    meta = usage_meta if isinstance(usage_meta, dict) else usage_meta.__dict__
                    output["usage"]["input"] = meta.get("prompt_token_count", 0) or 0
                    output["usage"]["output"] = (meta.get("candidates_token_count", 0) or 0) + (meta.get("thoughts_token_count", 0) or 0)
                    output["usage"]["cache_read"] = meta.get("cached_content_token_count", 0) or 0
                    output["usage"]["total_tokens"] = meta.get("total_token_count", 0) or 0
                    calculate_cost(model, output["usage"])

            if current_block:
                if current_block.get("type") == "text":
                    ev_stream.push({"type": "text_end", "content_index": block_idx(), "content": current_block.get("text", ""), "partial": output})
                else:
                    ev_stream.push({"type": "thinking_end", "content_index": block_idx(), "content": current_block.get("thinking", ""), "partial": output})

            if output["stop_reason"] in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for b in output["content"]:
                b.pop("index", None)
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            ev_stream.push({"type": "error", "reason": "error", "error": output})
            ev_stream.end(output)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_google_vertex(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for Vertex AI streaming."""
    base = build_base_options(model, options)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None

    if not reasoning:
        return stream_google_vertex(model, context, {**base_dict, "thinking": {"enabled": False}})

    effort = clamp_reasoning(reasoning) or "low"

    if _is_gemini3_pro(model) or _is_gemini3_flash(model):
        return stream_google_vertex(model, context, {
            **base_dict,
            "thinking": {"enabled": True, "level": _get_gemini3_thinking_level(effort, model)},
        })

    return stream_google_vertex(model, context, {
        **base_dict,
        "thinking": {
            "enabled": True,
            "budget_tokens": _get_google_budget(model, effort, getattr(options, "thinking_budgets", None)),
        },
    })


def _create_client(
    model: "Model",
    project: str,
    location: str,
    options_headers: dict[str, str] | None = None,
) -> Any:
    try:
        from google.genai import Client  # type: ignore[import]
    except ImportError:
        from google import genai  # type: ignore[import]
        Client = genai.Client

    headers = {**(getattr(model, "headers", None) or {}), **(options_headers or {})}
    return Client(
        vertexai=True,
        project=project,
        location=location,
        http_options={"headers": headers} if headers else None,
    )


def _resolve_project(opts: dict[str, Any]) -> str:
    project = (
        opts.get("project")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    if not project:
        raise ValueError(
            "Vertex AI requires a project ID. "
            "Set GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT or pass project in options."
        )
    return project


def _resolve_location(opts: dict[str, Any]) -> str:
    location = opts.get("location") or os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not location:
        raise ValueError(
            "Vertex AI requires a location. "
            "Set GOOGLE_CLOUD_LOCATION or pass location in options."
        )
    return location


def _build_params(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
) -> dict[str, Any]:
    contents = convert_messages(model, context)
    config: dict[str, Any] = {}

    if opts.get("temperature") is not None:
        config["temperature"] = opts["temperature"]
    if opts.get("max_tokens") is not None:
        config["max_output_tokens"] = opts["max_tokens"]
    if context.system_prompt:
        config["system_instruction"] = sanitize_surrogates(context.system_prompt)

    tools_list = getattr(context, "tools", None) or []
    if tools_list:
        converted = convert_tools(tools_list)
        if converted:
            config["tools"] = converted
        if opts.get("tool_choice"):
            config["tool_config"] = {
                "function_calling_config": {"mode": map_tool_choice(opts["tool_choice"])}
            }

    thinking_opts = opts.get("thinking")
    if thinking_opts and thinking_opts.get("enabled") and getattr(model, "reasoning", False):
        thinking_config: dict[str, Any] = {"include_thoughts": True}
        if thinking_opts.get("level") is not None:
            thinking_config["thinking_level"] = thinking_opts["level"]
        elif thinking_opts.get("budget_tokens") is not None:
            thinking_config["thinking_budget"] = thinking_opts["budget_tokens"]
        config["thinking_config"] = thinking_config

    return {"model": model.id, "contents": contents, "config": config}


def _is_gemini3_pro(model: "Model") -> bool:
    return "3-pro" in model.id


def _is_gemini3_flash(model: "Model") -> bool:
    return "3-flash" in model.id


def _get_gemini3_thinking_level(effort: str, model: "Model") -> str:
    if _is_gemini3_pro(model):
        if effort in ("minimal", "low"):
            return "LOW"
        return "HIGH"
    mapping = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    return mapping.get(effort, "LOW")


def _get_google_budget(
    model: "Model",
    effort: str,
    custom_budgets: dict[str, int] | None = None,
) -> int:
    if custom_budgets and effort in custom_budgets:
        return custom_budgets[effort]
    if "2.5-pro" in model.id:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}.get(effort, 2048)
    if "2.5-flash" in model.id:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}.get(effort, 2048)
    return -1
