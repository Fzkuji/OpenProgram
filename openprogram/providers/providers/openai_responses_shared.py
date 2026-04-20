"""
Shared utilities for OpenAI Responses API providers.

Handles message/tool conversion to Responses API format and streaming
event processing.

Mirrors openai-responses-shared.ts
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openprogram.providers.utils.json_parse import parse_streaming_json
from openprogram.providers.utils.sanitize_unicode import sanitize_surrogates

if TYPE_CHECKING:
    from openprogram.providers.types import (
        AssistantMessage,
        Context,
        Model,
        StopReason,
        TextContent,
        ThinkingContent,
        Tool,
        ToolCall,
        Usage,
    )
    from openprogram.providers.utils.event_stream import EventStream


# ---------------------------------------------------------------------------
# Hash utility
# ---------------------------------------------------------------------------

def _short_hash(s: str) -> str:
    """Fast deterministic hash to shorten long strings (mirrors shortHash in TS)."""
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    for ch in s:
        c = ord(ch)
        h1 = ((h1 ^ c) * 2654435761) & 0xFFFFFFFF
        h2 = ((h2 ^ c) * 1597334677) & 0xFFFFFFFF
    h1 = (((h1 ^ (h1 >> 16)) * 2246822507) & 0xFFFFFFFF) ^ (((h2 ^ (h2 >> 13)) * 3266489909) & 0xFFFFFFFF)
    h2 = (((h2 ^ (h2 >> 16)) * 2246822507) & 0xFFFFFFFF) ^ (((h1 ^ (h1 >> 13)) * 3266489909) & 0xFFFFFFFF)
    return format(h2 & 0xFFFFFFFF, "x") + format(h1 & 0xFFFFFFFF, "x")


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------

# Providers whose tool call IDs use the "callId|itemId" format
_RESPONSES_ALLOWED_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-responses", "openai-codex"})


def convert_responses_messages(
    model: "Model",
    context: "Context",
    allowed_tool_call_providers: frozenset[str] | None = None,
    include_system_prompt: bool = True,
) -> list[dict[str, Any]]:
    """Convert internal messages to OpenAI Responses API input format."""
    from openprogram.providers.providers.transform_messages import transform_messages

    if allowed_tool_call_providers is None:
        allowed_tool_call_providers = _RESPONSES_ALLOWED_TOOL_CALL_PROVIDERS

    def normalize_tool_call_id(id_: str) -> str:
        if model.provider not in allowed_tool_call_providers:
            return id_
        if "|" not in id_:
            return id_
        call_id, item_id_raw = id_.split("|", 1)
        sanitized_call = id_.replace("|", "_")[:64].rstrip("_")
        sanitized_item = item_id_raw
        # Sanitize
        sanitized_call = __import__("re").sub(r"[^a-zA-Z0-9_-]", "_", call_id)
        sanitized_item = __import__("re").sub(r"[^a-zA-Z0-9_-]", "_", item_id_raw)
        if not sanitized_item.startswith("fc"):
            sanitized_item = f"fc_{sanitized_item}"
        sanitized_call = sanitized_call[:64].rstrip("_")
        sanitized_item = sanitized_item[:64].rstrip("_")
        return f"{sanitized_call}|{sanitized_item}"

    messages: list[dict[str, Any]] = []
    if include_system_prompt and context.system_prompt:
        role = "developer" if getattr(model, "reasoning", None) else "system"
        messages.append({"role": role, "content": sanitize_surrogates(context.system_prompt)})

    transformed = transform_messages(context.messages, model, normalize_tool_call_id)

    for msg_index, msg in enumerate(transformed):
        role = getattr(msg, "role", None)

        if role == "user":
            content_val = msg.content
            if isinstance(content_val, str):
                messages.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": sanitize_surrogates(content_val)}],
                })
            else:
                content_items: list[dict[str, Any]] = []
                for item in content_val:
                    item_type = getattr(item, "type", None)
                    if item_type == "text":
                        content_items.append({"type": "input_text", "text": sanitize_surrogates(item.text)})
                    elif item_type == "image":
                        content_items.append({
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{item.mime_type};base64,{item.data}",
                        })
                if "image" not in (model.input or []):
                    content_items = [c for c in content_items if c["type"] != "input_image"]
                if not content_items:
                    continue
                messages.append({"role": "user", "content": content_items})

        elif role == "assistant":
            output: list[dict[str, Any]] = []
            is_different_model = (
                msg.model != model.id
                and msg.provider == model.provider
                and msg.api == model.api
            )

            for block in msg.content:
                block_type = getattr(block, "type", None)
                if block_type == "thinking":
                    sig = getattr(block, "thinking_signature", None)
                    if sig:
                        try:
                            reasoning_item = json.loads(sig)
                            output.append(reasoning_item)
                        except (json.JSONDecodeError, TypeError):
                            pass
                elif block_type == "text":
                    msg_id = getattr(block, "text_signature", None)
                    if not msg_id:
                        msg_id = f"msg_{msg_index}"
                    elif len(msg_id) > 64:
                        msg_id = f"msg_{_short_hash(msg_id)}"
                    output.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": sanitize_surrogates(getattr(block, "text", "")),
                            "annotations": [],
                        }],
                        "status": "completed",
                        "id": msg_id,
                    })
                elif block_type == "toolCall":
                    call_parts = block.id.split("|", 1)
                    call_id = call_parts[0]
                    item_id: str | None = call_parts[1] if len(call_parts) > 1 else None

                    if is_different_model and item_id and item_id.startswith("fc_"):
                        item_id = None

                    fc: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": block.name,
                        "arguments": json.dumps(getattr(block, "arguments", {}) or {}),
                    }
                    if item_id is not None:
                        fc["id"] = item_id
                    output.append(fc)

            if not output:
                continue
            messages.extend(output)

        elif role == "toolResult":
            text_result = "\n".join(
                c.text for c in msg.content if getattr(c, "type", None) == "text"
            )
            has_images = any(getattr(c, "type", None) == "image" for c in msg.content)
            has_text = bool(text_result)

            call_id = msg.tool_call_id.split("|")[0]
            messages.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": sanitize_surrogates(text_result if has_text else "(see attached image)"),
            })

            if has_images and "image" in (model.input or []):
                content_parts: list[dict[str, Any]] = [
                    {"type": "input_text", "text": "Attached image(s) from tool result:"}
                ]
                for block in msg.content:
                    if getattr(block, "type", None) == "image":
                        content_parts.append({
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{block.mime_type};base64,{block.data}",
                        })
                messages.append({"role": "user", "content": content_parts})

    return messages


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------

def convert_responses_tools(
    tools: "list[Tool]",
    strict: bool | None = False,
) -> list[dict[str, Any]]:
    """Convert tools to OpenAI Responses API function format."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "strict": strict if strict is not None else False,
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Stream processing
# ---------------------------------------------------------------------------

async def process_responses_stream(
    openai_stream: Any,
    output: "AssistantMessage",
    stream: "EventStream",
    model: "Model",
    service_tier: str | None = None,
    apply_service_tier_pricing: Any | None = None,
) -> None:
    """Process an OpenAI Responses API stream into our event stream format."""
    from openprogram.providers.models import calculate_cost

    current_item: dict[str, Any] | None = None
    current_block: Any | None = None
    blocks = output.content

    def block_index() -> int:
        return len(blocks) - 1

    async for event in openai_stream:
        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)

        if event_type == "response.output_item.added":
            item = event.get("item") if isinstance(event, dict) else getattr(event, "item", {})
            item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)

            if item_type == "reasoning":
                current_item = item if isinstance(item, dict) else item.__dict__
                current_block = {"type": "thinking", "thinking": ""}
                output.content.append(current_block)
                stream.push({"type": "thinking_start", "content_index": block_index(), "partial": output})

            elif item_type == "message":
                current_item = item if isinstance(item, dict) else item.__dict__
                current_block = {"type": "text", "text": ""}
                output.content.append(current_block)
                stream.push({"type": "text_start", "content_index": block_index(), "partial": output})

            elif item_type == "function_call":
                item_dict = item if isinstance(item, dict) else item.__dict__
                call_id = item_dict.get("call_id", "")
                item_id = item_dict.get("id", "")
                current_item = item_dict
                current_block = {
                    "type": "toolCall",
                    "id": f"{call_id}|{item_id}",
                    "name": item_dict.get("name", ""),
                    "arguments": {},
                    "partial_json": item_dict.get("arguments", ""),
                }
                output.content.append(current_block)
                stream.push({"type": "toolcall_start", "content_index": block_index(), "partial": output})

        elif event_type == "response.reasoning_summary_text.delta":
            if current_item and current_item.get("type") == "reasoning" and isinstance(current_block, dict) and current_block.get("type") == "thinking":
                delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", "")
                current_block["thinking"] = current_block.get("thinking", "") + delta
                stream.push({"type": "thinking_delta", "content_index": block_index(), "delta": delta, "partial": output})

        elif event_type == "response.reasoning_summary_part.done":
            if current_item and current_item.get("type") == "reasoning" and isinstance(current_block, dict) and current_block.get("type") == "thinking":
                current_block["thinking"] = current_block.get("thinking", "") + "\n\n"
                stream.push({"type": "thinking_delta", "content_index": block_index(), "delta": "\n\n", "partial": output})

        elif event_type == "response.output_text.delta":
            if current_item and current_item.get("type") == "message" and isinstance(current_block, dict) and current_block.get("type") == "text":
                delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", "")
                current_block["text"] = current_block.get("text", "") + delta
                stream.push({"type": "text_delta", "content_index": block_index(), "delta": delta, "partial": output})

        elif event_type == "response.refusal.delta":
            if current_item and current_item.get("type") == "message" and isinstance(current_block, dict) and current_block.get("type") == "text":
                delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", "")
                current_block["text"] = current_block.get("text", "") + delta
                stream.push({"type": "text_delta", "content_index": block_index(), "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.delta":
            if current_item and current_item.get("type") == "function_call" and isinstance(current_block, dict) and current_block.get("type") == "toolCall":
                delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", "")
                current_block["partial_json"] = current_block.get("partial_json", "") + delta
                current_block["arguments"] = parse_streaming_json(current_block["partial_json"])
                stream.push({"type": "toolcall_delta", "content_index": block_index(), "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.done":
            if isinstance(current_block, dict) and current_block.get("type") == "toolCall":
                args_str = event.get("arguments") if isinstance(event, dict) else getattr(event, "arguments", "")
                current_block["partial_json"] = args_str or ""
                current_block["arguments"] = parse_streaming_json(current_block["partial_json"])

        elif event_type == "response.output_item.done":
            item = event.get("item") if isinstance(event, dict) else getattr(event, "item", {})
            item_dict = item if isinstance(item, dict) else (item.__dict__ if item else {})
            item_type = item_dict.get("type")

            if item_type == "reasoning" and isinstance(current_block, dict) and current_block.get("type") == "thinking":
                summary = item_dict.get("summary") or []
                thinking_text = "\n\n".join(s.get("text", "") if isinstance(s, dict) else getattr(s, "text", "") for s in summary)
                current_block["thinking"] = thinking_text
                current_block["thinking_signature"] = json.dumps(item_dict)
                stream.push({"type": "thinking_end", "content_index": block_index(), "content": thinking_text, "partial": output})
                current_block = None

            elif item_type == "message" and isinstance(current_block, dict) and current_block.get("type") == "text":
                item_content = item_dict.get("content") or []
                text = "".join(
                    (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", ""))
                    + (c.get("refusal", "") if isinstance(c, dict) else getattr(c, "refusal", ""))
                    for c in item_content
                )
                current_block["text"] = text
                current_block["text_signature"] = item_dict.get("id", "")
                stream.push({"type": "text_end", "content_index": block_index(), "content": text, "partial": output})
                current_block = None

            elif item_type == "function_call":
                partial_json = current_block.get("partial_json", "") if isinstance(current_block, dict) else ""
                args_raw = partial_json or item_dict.get("arguments", "{}")
                args = parse_streaming_json(args_raw)
                tool_call = {
                    "type": "toolCall",
                    "id": f"{item_dict.get('call_id', '')}|{item_dict.get('id', '')}",
                    "name": item_dict.get("name", ""),
                    "arguments": args,
                }
                current_block = None
                stream.push({"type": "toolcall_end", "content_index": block_index(), "tool_call": tool_call, "partial": output})

        elif event_type == "response.completed":
            response = event.get("response") if isinstance(event, dict) else getattr(event, "response", None)
            if response:
                resp_dict = response if isinstance(response, dict) else response.__dict__
                usage_raw = resp_dict.get("usage")
                if usage_raw:
                    usage_dict = usage_raw if isinstance(usage_raw, dict) else usage_raw.__dict__
                    input_tokens = usage_dict.get("input_tokens", 0) or 0
                    output_tokens = usage_dict.get("output_tokens", 0) or 0
                    total_tokens = usage_dict.get("total_tokens", 0) or 0
                    details = usage_dict.get("input_tokens_details") or {}
                    details_dict = details if isinstance(details, dict) else details.__dict__
                    cached = details_dict.get("cached_tokens", 0) or 0

                    output.usage.input = input_tokens - cached
                    output.usage.output = output_tokens
                    output.usage.cache_read = cached
                    output.usage.cache_write = 0
                    output.usage.total_tokens = total_tokens

                calculate_cost(model, output.usage)

                if apply_service_tier_pricing:
                    tier = resp_dict.get("service_tier") or service_tier
                    apply_service_tier_pricing(output.usage, tier)

                status = resp_dict.get("status")
                output.stop_reason = _map_stop_reason(status)
                if any(getattr(b, "type", None) == "toolCall" or (isinstance(b, dict) and b.get("type") == "toolCall") for b in output.content) and output.stop_reason == "stop":
                    output.stop_reason = "toolUse"

        elif event_type == "error":
            code = event.get("code") if isinstance(event, dict) else getattr(event, "code", "")
            msg_text = event.get("message") if isinstance(event, dict) else getattr(event, "message", "Unknown error")
            raise RuntimeError(f"Error Code {code}: {msg_text}")

        elif event_type == "response.failed":
            raise RuntimeError("Unknown error")


def _map_stop_reason(status: str | None) -> "StopReason":
    if not status:
        return "stop"
    mapping: dict[str, "StopReason"] = {
        "completed": "stop",
        "incomplete": "length",
        "failed": "error",
        "cancelled": "error",
        "in_progress": "stop",
        "queued": "stop",
    }
    return mapping.get(status, "stop")
