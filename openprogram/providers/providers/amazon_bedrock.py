"""
AWS Bedrock Converse Stream API provider.

Streams responses from Bedrock models (Claude, etc.) with tool calling,
thinking/reasoning, and prompt caching support.

Mirrors amazon-bedrock.ts
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import calculate_cost
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.json_parse import parse_streaming_json
from openprogram.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.providers.types import (
        AssistantMessage,
        CacheRetention,
        Context,
        Model,
        SimpleStreamOptions,
        ThinkingBudgets,
        ThinkingLevel,
        Tool,
        Usage,
    )


def _new_usage() -> dict[str, Any]:
    return {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "total_tokens": 0,
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    }


def _supports_prompt_caching(model: "Model") -> bool:
    cost = getattr(model, "cost", None)
    if cost and (getattr(cost, "cache_read", 0) or getattr(cost, "cache_write", 0)):
        return True
    mid = model.id.lower()
    if "claude" in mid and (mid.count("-4-") or mid.count("-4.")):
        return True
    if "claude-3-7-sonnet" in mid or "claude-3-5-haiku" in mid:
        return True
    return False


def _supports_adaptive_thinking(model_id: str) -> bool:
    return "opus-4-6" in model_id or "opus-4.6" in model_id


def _supports_thinking_signature(model_id: str) -> bool:
    mid = model_id.lower()
    return "anthropic.claude" in mid or "anthropic/claude" in mid


def _resolve_cache_retention(cache_retention: "CacheRetention | None") -> "CacheRetention":
    if cache_retention:
        return cache_retention
    if os.environ.get("PI_CACHE_RETENTION") == "long":
        return "long"
    return "short"


def _normalize_tool_call_id(id_: str) -> str:
    import re
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", id_)
    return sanitized[:64]


def _map_thinking_level_to_effort(level: "ThinkingLevel | None") -> str:
    if level in ("minimal", "low"):
        return "low"
    if level == "medium":
        return "medium"
    if level == "high":
        return "high"
    if level == "xhigh":
        return "max"
    return "high"


def stream_bedrock(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream responses from AWS Bedrock Converse Stream API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    async def _run() -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 is required for Bedrock provider: pip install boto3")

        from openprogram.providers.providers.transform_messages import transform_messages

        output: dict[str, Any] = {
            "role": "assistant",
            "content": [],
            "api": "bedrock-converse-stream",
            "provider": model.provider,
            "model": model.id,
            "usage": _new_usage(),
            "stop_reason": "stop",
            "timestamp": int(time.time() * 1000),
        }
        blocks: list[dict[str, Any]] = output["content"]

        region = (
            opts.get("region")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )

        client_kwargs: dict[str, Any] = {"region_name": region}
        if opts.get("profile"):
            import boto3.session
            session = boto3.session.Session(profile_name=opts["profile"])
            client = session.client("bedrock-runtime", region_name=region)
        else:
            if os.environ.get("AWS_BEDROCK_SKIP_AUTH") == "1":
                client_kwargs["aws_access_key_id"] = "dummy-access-key"
                client_kwargs["aws_secret_access_key"] = "dummy-secret-key"
            client = boto3.client("bedrock-runtime", **client_kwargs)

        cache_retention = _resolve_cache_retention(opts.get("cache_retention"))
        try:
            messages = _convert_messages_bedrock(context, model, cache_retention)
            system_blocks = _build_system_prompt_bedrock(context.system_prompt, model, cache_retention)
            inference_config: dict[str, Any] = {}
            if opts.get("max_tokens"):
                inference_config["maxTokens"] = opts["max_tokens"]
            if opts.get("temperature") is not None:
                inference_config["temperature"] = opts["temperature"]

            tool_config = _convert_tool_config_bedrock(
                getattr(context, "tools", None),
                opts.get("tool_choice"),
            )
            add_fields = _build_additional_model_request_fields(model, opts)

            request: dict[str, Any] = {
                "modelId": model.id,
                "messages": messages,
                "inferenceConfig": inference_config,
            }
            if system_blocks:
                request["system"] = system_blocks
            if tool_config:
                request["toolConfig"] = tool_config
            if add_fields:
                request["additionalModelRequestFields"] = add_fields

            if opts.get("on_payload"):
                opts["on_payload"](request)

            response = client.converse_stream(**request)

            for item in response["stream"]:
                if "messageStart" in item:
                    ev_stream.push({"type": "start", "partial": output})
                elif "contentBlockStart" in item:
                    _handle_block_start_bedrock(item["contentBlockStart"], blocks, output, ev_stream)
                elif "contentBlockDelta" in item:
                    _handle_block_delta_bedrock(item["contentBlockDelta"], blocks, output, ev_stream)
                elif "contentBlockStop" in item:
                    _handle_block_stop_bedrock(item["contentBlockStop"], blocks, output, ev_stream)
                elif "messageStop" in item:
                    output["stop_reason"] = _map_stop_reason_bedrock(item["messageStop"].get("stopReason"))
                elif "metadata" in item:
                    _handle_metadata_bedrock(item["metadata"], model, output)
                elif "internalServerException" in item:
                    raise RuntimeError(f"Internal server error: {item['internalServerException'].get('message', '')}")
                elif "modelStreamErrorException" in item:
                    raise RuntimeError(f"Model stream error: {item['modelStreamErrorException'].get('message', '')}")
                elif "validationException" in item:
                    raise RuntimeError(f"Validation error: {item['validationException'].get('message', '')}")
                elif "throttlingException" in item:
                    raise RuntimeError(f"Throttling error: {item['throttlingException'].get('message', '')}")
                elif "serviceUnavailableException" in item:
                    raise RuntimeError(f"Service unavailable: {item['serviceUnavailableException'].get('message', '')}")

            output["stop_reason"] = output.get("stop_reason", "stop")
            if output["stop_reason"] in ("error", "aborted"):
                raise RuntimeError("An unknown error occurred")

            ev_stream.push({"type": "done", "reason": output["stop_reason"], "message": output})
            ev_stream.end(output)

        except Exception as exc:
            for block in blocks:
                block.pop("index", None)
                block.pop("partial_json", None)
            output["stop_reason"] = "error"
            output["error_message"] = str(exc)
            ev_stream.push({"type": "error", "reason": "error", "error": output})
            ev_stream.end(output)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_bedrock(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for Bedrock streaming."""
    from openprogram.providers.providers.simple_options import adjust_max_tokens_for_thinking, build_base_options, clamp_reasoning

    base = build_base_options(model, options)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None

    if not reasoning:
        base_dict.pop("reasoning", None)
        return stream_bedrock(model, context, base_dict)

    model_id = model.id
    if "anthropic.claude" in model_id or "anthropic/claude" in model_id:
        if _supports_adaptive_thinking(model_id):
            return stream_bedrock(model, context, {**base_dict, "reasoning": reasoning})

        adj_max, adj_budget = adjust_max_tokens_for_thinking(
            base_dict.get("max_tokens", 0) or 0,
            model.max_tokens,
            reasoning,
            getattr(options, "thinking_budgets", None),
        )
        level_key = clamp_reasoning(reasoning) or "low"
        budgets = {**(getattr(options, "thinking_budgets", None) or {}), level_key: adj_budget}
        return stream_bedrock(model, context, {
            **base_dict,
            "max_tokens": adj_max,
            "reasoning": reasoning,
            "thinking_budgets": budgets,
        })

    return stream_bedrock(model, context, {**base_dict, "reasoning": reasoning})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_messages_bedrock(
    context: "Context",
    model: "Model",
    cache_retention: "CacheRetention",
) -> list[dict[str, Any]]:
    from openprogram.providers.providers.transform_messages import transform_messages

    result: list[dict[str, Any]] = []
    transformed = transform_messages(context.messages, model, _normalize_tool_call_id)

    i = 0
    while i < len(transformed):
        msg = transformed[i]
        role = getattr(msg, "role", None)

        if role == "user":
            content_val = msg.content
            if isinstance(content_val, str):
                bedrock_content = [{"text": sanitize_surrogates(content_val)}]
            else:
                bedrock_content = []
                for c in content_val:
                    c_type = getattr(c, "type", None)
                    if c_type == "text":
                        bedrock_content.append({"text": sanitize_surrogates(c.text)})
                    elif c_type == "image":
                        bedrock_content.append({"image": _create_image_block_bedrock(c.mime_type, c.data)})
            result.append({"role": "user", "content": bedrock_content})

        elif role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            for c in msg.content:
                c_type = getattr(c, "type", None)
                if c_type == "text":
                    txt = getattr(c, "text", "") or ""
                    if not txt.strip():
                        continue
                    content_blocks.append({"text": sanitize_surrogates(txt)})
                elif c_type == "toolCall":
                    content_blocks.append({
                        "toolUse": {
                            "toolUseId": c.id,
                            "name": c.name,
                            "input": getattr(c, "arguments", {}) or {},
                        }
                    })
                elif c_type == "thinking":
                    thinking = getattr(c, "thinking", "") or ""
                    if not thinking.strip():
                        continue
                    if _supports_thinking_signature(model.id):
                        content_blocks.append({
                            "reasoningContent": {
                                "reasoningText": {
                                    "text": sanitize_surrogates(thinking),
                                    "signature": getattr(c, "thinking_signature", None),
                                }
                            }
                        })
                    else:
                        content_blocks.append({
                            "reasoningContent": {
                                "reasoningText": {"text": sanitize_surrogates(thinking)}
                            }
                        })
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})

        elif role == "toolResult":
            tool_results: list[dict[str, Any]] = []

            def _make_tool_result(tr_msg: Any) -> dict[str, Any]:
                content_items = []
                for c in tr_msg.content:
                    c_type = getattr(c, "type", None)
                    if c_type == "image":
                        content_items.append({"image": _create_image_block_bedrock(c.mime_type, c.data)})
                    else:
                        content_items.append({"text": sanitize_surrogates(c.text)})
                return {
                    "toolResult": {
                        "toolUseId": tr_msg.tool_call_id,
                        "content": content_items,
                        "status": "error" if tr_msg.is_error else "success",
                    }
                }

            tool_results.append(_make_tool_result(msg))
            j = i + 1
            while j < len(transformed) and getattr(transformed[j], "role", None) == "toolResult":
                tool_results.append(_make_tool_result(transformed[j]))
                j += 1
            i = j - 1
            result.append({"role": "user", "content": tool_results})

        i += 1

    # Add cache point to last user message for supported models
    if cache_retention != "none" and _supports_prompt_caching(model) and result:
        last = result[-1]
        if last.get("role") == "user":
            cache_point: dict[str, Any] = {"type": "default"}
            if cache_retention == "long":
                cache_point["ttl"] = "ONE_HOUR"
            last["content"].append({"cachePoint": cache_point})

    return result


def _build_system_prompt_bedrock(
    system_prompt: str | None,
    model: "Model",
    cache_retention: "CacheRetention",
) -> list[dict[str, Any]] | None:
    if not system_prompt:
        return None
    blocks: list[dict[str, Any]] = [{"text": sanitize_surrogates(system_prompt)}]
    if cache_retention != "none" and _supports_prompt_caching(model):
        cache_point: dict[str, Any] = {"type": "default"}
        if cache_retention == "long":
            cache_point["ttl"] = "ONE_HOUR"
        blocks.append({"cachePoint": cache_point})
    return blocks


def _create_image_block_bedrock(mime_type: str, data: str) -> dict[str, Any]:
    fmt_map = {
        "image/jpeg": "jpeg", "image/jpg": "jpeg",
        "image/png": "png", "image/gif": "gif",
        "image/webp": "webp",
    }
    fmt = fmt_map.get(mime_type.lower(), "jpeg")
    return {"format": fmt, "source": {"bytes": data}}


def _convert_tool_config_bedrock(
    tools: list | None,
    tool_choice: Any,
) -> dict[str, Any] | None:
    if not tools or tool_choice == "none":
        return None
    bedrock_tools = [
        {
            "toolSpec": {
                "name": t.name,
                "description": t.description,
                "inputSchema": {"json": t.parameters},
            }
        }
        for t in tools
    ]
    config: dict[str, Any] = {"tools": bedrock_tools}
    if tool_choice == "auto" or tool_choice is None:
        config["toolChoice"] = {"auto": {}}
    elif tool_choice == "any":
        config["toolChoice"] = {"any": {}}
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
        config["toolChoice"] = {"tool": {"name": tool_choice["name"]}}
    return config


def _build_additional_model_request_fields(
    model: "Model",
    opts: dict[str, Any],
) -> dict[str, Any] | None:
    reasoning = opts.get("reasoning")
    if not reasoning:
        return None
    model_id = model.id
    if "anthropic.claude" not in model_id and "anthropic/claude" not in model_id:
        return None

    if _supports_adaptive_thinking(model_id):
        effort = _map_thinking_level_to_effort(reasoning)
        return {"thinking": {"type": "adaptive", "effort": effort}}
    else:
        from openprogram.providers.providers.simple_options import adjust_max_tokens_for_thinking, clamp_reasoning
        budgets = opts.get("thinking_budgets") or {}
        max_t = opts.get("max_tokens", 0) or 0
        _adj_max, budget = adjust_max_tokens_for_thinking(
            max_t, model.max_tokens, reasoning, budgets
        )
        level_key = clamp_reasoning(reasoning) or "low"
        final_budget = budgets.get(level_key, budget)
        return {"thinking": {"type": "enabled", "budget_tokens": final_budget}}


def _handle_block_start_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    index = event.get("contentBlockIndex", 0)
    start = event.get("start", {})
    if start.get("toolUse"):
        block = {
            "type": "toolCall",
            "id": start["toolUse"].get("toolUseId", ""),
            "name": start["toolUse"].get("name", ""),
            "arguments": {},
            "partial_json": "",
            "index": index,
        }
        output["content"].append(block)
        ev_stream.push({"type": "toolcall_start", "content_index": len(blocks) - 1, "partial": output})


def _handle_block_delta_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    content_block_index = event.get("contentBlockIndex", 0)
    delta = event.get("delta", {})

    idx = next((i for i, b in enumerate(blocks) if b.get("index") == content_block_index), -1)
    block = blocks[idx] if idx >= 0 else None

    if "text" in delta and delta["text"] is not None:
        if block is None:
            new_block: dict[str, Any] = {"type": "text", "text": "", "index": content_block_index}
            output["content"].append(new_block)
            idx = len(blocks) - 1
            block = blocks[idx]
            ev_stream.push({"type": "text_start", "content_index": idx, "partial": output})
        if block and block.get("type") == "text":
            block["text"] = block.get("text", "") + delta["text"]
            ev_stream.push({"type": "text_delta", "content_index": idx, "delta": delta["text"], "partial": output})

    elif delta.get("toolUse") and block and block.get("type") == "toolCall":
        block["partial_json"] = (block.get("partial_json") or "") + (delta["toolUse"].get("input") or "")
        block["arguments"] = parse_streaming_json(block["partial_json"])
        ev_stream.push({"type": "toolcall_delta", "content_index": idx, "delta": delta["toolUse"].get("input", ""), "partial": output})

    elif delta.get("reasoningContent"):
        if block is None:
            new_block = {"type": "thinking", "thinking": "", "thinking_signature": "", "index": content_block_index}
            output["content"].append(new_block)
            idx = len(blocks) - 1
            block = blocks[idx]
            ev_stream.push({"type": "thinking_start", "content_index": idx, "partial": output})
        if block and block.get("type") == "thinking":
            rc = delta["reasoningContent"]
            if rc.get("text"):
                block["thinking"] = block.get("thinking", "") + rc["text"]
                ev_stream.push({"type": "thinking_delta", "content_index": idx, "delta": rc["text"], "partial": output})
            if rc.get("signature"):
                block["thinking_signature"] = (block.get("thinking_signature") or "") + rc["signature"]


def _handle_block_stop_bedrock(
    event: dict[str, Any],
    blocks: list[dict[str, Any]],
    output: dict[str, Any],
    ev_stream: EventStream,
) -> None:
    content_block_index = event.get("contentBlockIndex", 0)
    idx = next((i for i, b in enumerate(blocks) if b.get("index") == content_block_index), -1)
    if idx < 0:
        return
    block = blocks[idx]
    block.pop("index", None)

    btype = block.get("type")
    if btype == "text":
        ev_stream.push({"type": "text_end", "content_index": idx, "content": block.get("text", ""), "partial": output})
    elif btype == "thinking":
        ev_stream.push({"type": "thinking_end", "content_index": idx, "content": block.get("thinking", ""), "partial": output})
    elif btype == "toolCall":
        block["arguments"] = parse_streaming_json(block.get("partial_json", ""))
        block.pop("partial_json", None)
        ev_stream.push({"type": "toolcall_end", "content_index": idx, "tool_call": block, "partial": output})


def _handle_metadata_bedrock(
    event: dict[str, Any],
    model: "Model",
    output: dict[str, Any],
) -> None:
    usage = event.get("usage", {})
    if usage:
        output["usage"]["input"] = usage.get("inputTokens", 0) or 0
        output["usage"]["output"] = usage.get("outputTokens", 0) or 0
        output["usage"]["cache_read"] = usage.get("cacheReadInputTokens", 0) or 0
        output["usage"]["cache_write"] = usage.get("cacheWriteInputTokens", 0) or 0
        total = usage.get("totalTokens", 0) or output["usage"]["input"] + output["usage"]["output"]
        output["usage"]["total_tokens"] = total
        calculate_cost(model, output["usage"])


def _map_stop_reason_bedrock(reason: str | None) -> str:
    mapping = {
        "end_turn": "stop",
        "tool_use": "toolUse",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "guardrail_intervened": "error",
        "content_filtered": "error",
    }
    return mapping.get(reason or "", "stop")
