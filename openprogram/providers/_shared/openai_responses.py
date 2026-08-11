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
def convert_responses_messages(
    model: "Model",
    context: "Context",
    include_system_prompt: bool = True,
) -> list[dict[str, Any]]:
    """Convert internal messages to OpenAI Responses API input format."""
    from openprogram.providers._shared.transform_messages import transform_messages

    # transform_messages calls this with 3 args (id, model, msg) per its
    # NormalizeToolCallIdFn signature, and ONLY for cross-model replay —
    # same-model turns keep their native ids untouched. Every Responses
    # endpoint (official or custom relay) caps call_id at 64 chars, so
    # normalize unconditionally: a foreign id is meaningless to the
    # target upstream anyway.
    def normalize_tool_call_id(id_: str, _model=None, _msg=None) -> str:
        import re
        if "|" not in id_:
            # Single foreign id (Anthropic toolu_..., synthetic, ...):
            # pass through when it fits, otherwise hash it
            # deterministically so the call/result pair stays matched.
            if len(id_) <= 64 and re.match(r"^[a-zA-Z0-9_-]+$", id_):
                return id_
            import hashlib
            return "tc_" + hashlib.sha256(id_.encode()).hexdigest()[:60]
        call_id, item_id_raw = id_.split("|", 1)
        sanitized_call = re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)
        sanitized_item = re.sub(r"[^a-zA-Z0-9_-]", "_", item_id_raw)
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
            # A turn from any OTHER model (different id, provider or api)
            # carries fc_ item ids the target upstream never issued —
            # replaying them risks "Item not found". Only a same-model
            # turn keeps its item id.
            is_foreign_turn = not (
                msg.model == model.id
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
                            # store=false responses don't persist items, so
                            # their server-assigned `id` won't resolve on the
                            # next call ("Item not found"). Strip it — the
                            # encrypted_content payload is self-contained.
                            if isinstance(reasoning_item, dict):
                                reasoning_item.pop("id", None)
                            output.append(reasoning_item)
                        except (json.JSONDecodeError, TypeError):
                            pass
                elif block_type == "text":
                    msg_id = getattr(block, "text_signature", None)
                    if not msg_id:
                        msg_id = f"msg_{msg_index}"
                    elif len(msg_id) > 64:
                        msg_id = f"msg_{_short_hash(msg_id)}"
                    # NOTE: no "status" field here. `status` is an OUTPUT-only
                    # field; some Responses endpoints (notably openai-codex's
                    # /responses) reject it on an INPUT item with
                    # "Unknown parameter: input[N].status". An input
                    # message item never needs it.
                    output.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": sanitize_surrogates(getattr(block, "text", "")),
                            "annotations": [],
                        }],
                        "id": msg_id,
                    })
                elif block_type == "toolCall":
                    call_parts = block.id.split("|", 1)
                    call_id = call_parts[0]
                    item_id: str | None = call_parts[1] if len(call_parts) > 1 else None

                    if is_foreign_turn and item_id and item_id.startswith("fc_"):
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
    api: str | None = "openai-responses",
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convert tools to OpenAI Responses API function format.

    Schema dialect + the ``"strict"`` flag are both decided by the
    unified ``providers._schema`` layer keyed on ``(api, model_id)`` —
    the SAME path the Chat Completions builder uses, so the two no
    longer drift (previously this defaulted strict off ``strict_tools_enabled``
    while completions gated on ``api_wants_strict``). Callers thread
    their ``model.api`` / ``model.id``; the api default keeps old
    call-by-position working.
    """
    from openprogram.providers._schema import normalize_for, wants_strict_flag

    use_strict = wants_strict_flag(api, model_id)
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": normalize_for(api, t.parameters, model_id),
            "strict": use_strict,
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
    signal: Any | None = None,
) -> None:
    """Process an OpenAI Responses API stream into our event stream format.

    Blocks are located by the event's ``output_index`` (stored as
    ``"index"`` on each block, popped when the item completes — the
    amazon_bedrock pattern), NOT by a single "current block" cursor.
    With parallel tool calls the API interleaves ``output_item.added``
    idx0/idx1 with their ``function_call_arguments.delta`` events; a
    cursor routed idx0's deltas onto idx1's block and both tool calls
    finalized with empty arguments.

    ``signal`` is the caller's cancel event (``SimpleStreamOptions.signal``);
    when it is set mid-stream we raise :class:`StreamAborted` so the
    provider can finalize the turn as ``stop_reason="aborted"``.
    """
    from openprogram.providers.models import calculate_cost
    from openprogram.providers.utils.errors import StreamAborted

    blocks = output.content
    refusal_seen = False

    def _get(ev: Any, name: str, default: Any = None) -> Any:
        return ev.get(name, default) if isinstance(ev, dict) else getattr(ev, name, default)

    def _find(idx: Any, want_type: str) -> int:
        """Index into ``blocks`` of the open block for output item ``idx``.

        Falls back to the most recent still-open block of ``want_type``
        when the event carries no ``output_index`` (older event shapes).
        A block is "open" while its ``"index"`` key is present.
        """
        if idx is not None:
            i = next(
                (i for i, b in enumerate(blocks)
                 if isinstance(b, dict) and b.get("index") == idx),
                -1,
            )
            if i >= 0:
                return i
        for i in range(len(blocks) - 1, -1, -1):
            b = blocks[i]
            if isinstance(b, dict) and b.get("type") == want_type and "index" in b:
                return i
        return -1

    def _apply_response_terminal(response: Any) -> None:
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

        details = resp_dict.get("incomplete_details")
        details_dict = details if isinstance(details, dict) else getattr(details, "__dict__", {})
        incomplete_reason = details_dict.get("reason")
        if refusal_seen or incomplete_reason == "content_filter":
            output.stop_reason = "error"
        elif incomplete_reason == "max_output_tokens":
            output.stop_reason = "length"
        else:
            output.stop_reason = _map_stop_reason(resp_dict.get("status"))
        if any(
            getattr(block, "type", None) == "toolCall"
            or (isinstance(block, dict) and block.get("type") == "toolCall")
            for block in output.content
        ) and output.stop_reason == "stop":
            output.stop_reason = "toolUse"

        output.content = _finalize_content_blocks(output.content)

    async for event in openai_stream:
        if signal is not None and callable(getattr(signal, "is_set", None)) and signal.is_set():
            raise StreamAborted("stream cancelled by caller signal")

        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)

        if event_type == "response.output_item.added":
            item = _get(event, "item", {})
            item_dict = item if isinstance(item, dict) else (item.__dict__ if item else {})
            item_type = item_dict.get("type")
            out_idx = _get(event, "output_index")
            if out_idx is None:
                out_idx = len(blocks)

            if item_type == "reasoning":
                blocks.append({"type": "thinking", "thinking": "", "index": out_idx})
                stream.push({"type": "thinking_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "message":
                blocks.append({"type": "text", "text": "", "index": out_idx})
                stream.push({"type": "text_start", "content_index": len(blocks) - 1, "partial": output})

            elif item_type == "function_call":
                call_id = item_dict.get("call_id", "")
                item_id = item_dict.get("id", "")
                blocks.append({
                    "type": "toolCall",
                    "id": f"{call_id}|{item_id}",
                    "name": item_dict.get("name", ""),
                    "arguments": {},
                    "partial_json": item_dict.get("arguments", ""),
                    "index": out_idx,
                })
                stream.push({"type": "toolcall_start", "content_index": len(blocks) - 1, "partial": output})

        elif event_type == "response.reasoning_summary_text.delta":
            i = _find(_get(event, "output_index"), "thinking")
            if i >= 0 and blocks[i].get("type") == "thinking":
                delta = _get(event, "delta", "")
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + delta
                stream.push({"type": "thinking_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.reasoning_summary_part.done":
            i = _find(_get(event, "output_index"), "thinking")
            if i >= 0 and blocks[i].get("type") == "thinking":
                blocks[i]["thinking"] = blocks[i].get("thinking", "") + "\n\n"
                stream.push({"type": "thinking_delta", "content_index": i, "delta": "\n\n", "partial": output})

        elif event_type in ("response.output_text.delta", "response.refusal.delta"):
            i = _find(_get(event, "output_index"), "text")
            if i >= 0 and blocks[i].get("type") == "text":
                delta = _get(event, "delta", "")
                blocks[i]["text"] = blocks[i].get("text", "") + delta
                stream.push({"type": "text_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.delta":
            i = _find(_get(event, "output_index"), "toolCall")
            if i >= 0 and blocks[i].get("type") == "toolCall":
                delta = _get(event, "delta", "")
                blocks[i]["partial_json"] = blocks[i].get("partial_json", "") + delta
                blocks[i]["arguments"] = parse_streaming_json(blocks[i]["partial_json"])
                stream.push({"type": "toolcall_delta", "content_index": i, "delta": delta, "partial": output})

        elif event_type == "response.function_call_arguments.done":
            i = _find(_get(event, "output_index"), "toolCall")
            if i >= 0 and blocks[i].get("type") == "toolCall":
                args_str = _get(event, "arguments", "")
                blocks[i]["partial_json"] = args_str or ""
                blocks[i]["arguments"] = parse_streaming_json(blocks[i]["partial_json"])

        elif event_type == "response.output_item.done":
            item = _get(event, "item", {})
            item_dict = item if isinstance(item, dict) else (item.__dict__ if item else {})
            item_type = item_dict.get("type")
            out_idx = _get(event, "output_index")

            if item_type == "reasoning":
                i = _find(out_idx, "thinking")
                if i >= 0 and blocks[i].get("type") == "thinking":
                    block = blocks[i]
                    summary = item_dict.get("summary") or []
                    thinking_text = "\n\n".join(s.get("text", "") if isinstance(s, dict) else getattr(s, "text", "") for s in summary).strip()
                    # Codex returns the DONE reasoning item with encrypted_content
                    # and an EMPTY summary array — the readable text only arrived
                    # via reasoning_summary_text.delta. Overwriting unconditionally
                    # clobbers that accumulated text to "", the thinking block then
                    # fails the `if _t:` guard at persist time, and neither the
                    # live blocks nor the DAG node ever carry the thinking — the
                    # UI can't collapse it into the "Thinking ×1" strip. Keep the
                    # deltas when the summary is empty.
                    if thinking_text:
                        block["thinking"] = thinking_text
                    block["thinking_signature"] = json.dumps(item_dict)
                    block.pop("index", None)
                    stream.push({"type": "thinking_end", "content_index": i, "content": block.get("thinking", ""), "partial": output})

            elif item_type == "message":
                refusal_seen = any(
                    (
                        c.get("type") == "refusal" or bool(c.get("refusal"))
                        if isinstance(c, dict)
                        else getattr(c, "type", None) == "refusal"
                        or bool(getattr(c, "refusal", None))
                    )
                    for c in (item_dict.get("content") or [])
                ) or refusal_seen
                i = _find(out_idx, "text")
                if i >= 0 and blocks[i].get("type") == "text":
                    block = blocks[i]
                    item_content = item_dict.get("content") or []
                    text = "".join(
                        (c.get("text", "") if isinstance(c, dict) else getattr(c, "text", ""))
                        + (c.get("refusal", "") if isinstance(c, dict) else getattr(c, "refusal", ""))
                        for c in item_content
                    )
                    block["text"] = text
                    block["text_signature"] = item_dict.get("id", "")
                    block.pop("index", None)
                    stream.push({"type": "text_end", "content_index": i, "content": text, "partial": output})

            elif item_type == "function_call":
                i = _find(out_idx, "toolCall")
                if i >= 0 and blocks[i].get("type") == "toolCall":
                    # Mutate the block in output.content so the finalized
                    # message carries the parsed args, not the empty stub
                    # from response.output_item.added.
                    block = blocks[i]
                    args_raw = block.get("partial_json", "") or item_dict.get("arguments", "{}")
                    args = parse_streaming_json(args_raw)
                    block["arguments"] = args
                    block.pop("partial_json", None)
                    block.pop("index", None)
                    tool_call = {
                        "type": "toolCall",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": args,
                    }
                    stream.push({"type": "toolcall_end", "content_index": i, "tool_call": tool_call, "partial": output})
                else:
                    args = parse_streaming_json(item_dict.get("arguments", "{}"))
                    tool_call = {
                        "type": "toolCall",
                        "id": f"{item_dict.get('call_id', '')}|{item_dict.get('id', '')}",
                        "name": item_dict.get("name", ""),
                        "arguments": args,
                    }
                    stream.push({"type": "toolcall_end", "content_index": len(blocks) - 1, "tool_call": tool_call, "partial": output})

        elif event_type in ("response.completed", "response.incomplete"):
            # Items that never saw output_item.done keep no bookkeeping key.
            for b in blocks:
                if isinstance(b, dict):
                    b.pop("index", None)
            response = event.get("response") if isinstance(event, dict) else getattr(event, "response", None)
            if response:
                _apply_response_terminal(response)

        elif event_type == "error":
            code = event.get("code") if isinstance(event, dict) else getattr(event, "code", "")
            msg_text = event.get("message") if isinstance(event, dict) else getattr(event, "message", "Unknown error")
            # Empty error event (both code and message null) is a transient
            # backend hiccup — codex intermittently emits these mid-stream on
            # large / tool-bearing requests while small requests succeed.
            # Surface it as a RETRYABLE stream error so retry_stream backs off
            # and tries again (which usually succeeds), instead of either a
            # bare RuntimeError (treated non-retryable -> killed the run) or a
            # doomed no-backoff storm. A real error (with a code/message) stays
            # a hard failure.
            if not code and not msg_text:
                from openprogram.providers.utils.stream_retry import ProviderStreamError
                raise ProviderStreamError(
                    "empty error event (transient backend hiccup)",
                    retryable=not bool(getattr(output, "content", None)),
                    # This failure is time-windowed — give the backend a
                    # longer floor before retrying (retry_after_s is honored
                    # as a lower bound by stream_backoff_seconds) so we probe
                    # a recovered window instead of hammering the bad one.
                    retry_after_s=3.0,
                )
            raise RuntimeError(f"Error Code {code}: {msg_text}")

        elif event_type == "response.failed":
            raise RuntimeError("Unknown error")


def _finalize_content_blocks(blocks: list) -> list:
    """Coerce streaming-stage dict blocks into the Pydantic content variants."""
    from openprogram.providers.types import TextContent, ThinkingContent, ToolCall

    finalized: list = []
    for b in blocks:
        if not isinstance(b, dict):
            finalized.append(b)
            continue
        btype = b.get("type")
        try:
            if btype == "text":
                finalized.append(TextContent.model_validate(b))
            elif btype == "thinking":
                finalized.append(ThinkingContent.model_validate(b))
            elif btype == "toolCall":
                args = b.get("arguments")
                if not isinstance(args, dict):
                    # parse_streaming_json can yield non-dict for malformed input;
                    # ToolCall schema requires a dict, so fall back to empty.
                    b = {**b, "arguments": {}}
                b.pop("partial_json", None)
                finalized.append(ToolCall.model_validate(b))
            else:
                finalized.append(b)
        except Exception:
            finalized.append(b)
    return finalized


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
