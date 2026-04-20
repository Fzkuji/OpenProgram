"""
Anthropic Messages API provider — mirrors packages/ai/src/providers/anthropic.ts

Full parity including:
- OAuth token detection (sk-ant-oat) → adaptive thinking effort levels
- Cache control retention (ephemeral / 1h)
- Beta headers: fine-grained-tool-streaming + interleaved-thinking
- sanitize_surrogates on all text content
- Empty content block filtering
- Usage capture from message_start event (not just end)
- All stop reasons: pause_turn, sensitive, refusal
- Claude Code tool name normalization for OAuth tokens
"""
from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator

import anthropic as _anthropic

from ..types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    EventDone,
    EventError,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventThinkingStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventToolCallStart,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from ..utils.event_stream import EventStream
from ..utils.json_parse import parse_partial_json, parse_streaming_json
from ..utils.sanitize_unicode import sanitize_surrogates
from .._shared.transform_messages import transform_messages as _transform_messages

# Anthropic beta features
_BETA_FINE_GRAINED = "fine-grained-tool-streaming-2025-05-14"
_BETA_INTERLEAVED = "interleaved-thinking-2025-05-14"
_BETA_OAUTH = "oauth-2025-04-20"
_BETA_CLAUDE_CODE = "claude-code-20250219"

# Claude Code version for OAuth stealth mode
_CLAUDE_CODE_VERSION = "2.1.62"

# Claude Code canonical tool name lookup (case-insensitive → canonical)
_CLAUDE_CODE_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "KillShell",
    "NotebookEdit", "Skill", "Task", "TaskOutput", "TodoWrite",
    "WebFetch", "WebSearch",
]
_CC_TOOL_LOOKUP = {t.lower(): t for t in _CLAUDE_CODE_TOOLS}


def _to_claude_code_name(name: str) -> str:
    """Convert tool name to Claude Code canonical casing."""
    return _CC_TOOL_LOOKUP.get(name.lower(), name)


def _from_claude_code_name(name: str, tools: list | None = None) -> str:
    """Map Claude Code tool name back to registered tool name."""
    if tools:
        lower = name.lower()
        for tool in tools:
            tname = tool.name if hasattr(tool, "name") else tool.get("name", "")
            if tname.lower() == lower:
                return tname
    return name


def _is_oauth_token(api_key: str) -> bool:
    """Check if the API key is an OAuth token (sk-ant-oat prefix)."""
    return "sk-ant-oat" in api_key


def _sanitize_surrogates(text: str) -> str:
    """
    Remove lone surrogate characters that would cause JSON encoding failures.
    Mirrors sanitizeSurrogates() in TypeScript.
    """
    # Replace lone surrogates (U+D800–U+DFFF) with U+FFFD
    return re.sub(r"[\ud800-\udfff]", "\ufffd", text)


# Thinking token budgets per level (budget-based models)
_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 4096,
    "medium": 8192,
    "high": 16000,
    "xhigh": 32000,
}

# Effort levels for adaptive thinking (Opus 4.6+)
_EFFORT_MAP = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
}

# Stop reason mapping from Anthropic to pi_ai (matches TS exactly)
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "pause_turn": "stop",
    "sensitive": "error",
    "refusal": "error",
    "stop_sequence": "stop",
}


def _normalize_tool_call_id(id_: str, model: Model, source: AssistantMessage) -> str:
    """Normalize tool call IDs for Anthropic (max 64 chars, alphanum + _ -)."""
    import re as _re
    if len(id_) <= 64 and _re.match(r"^[a-zA-Z0-9_-]+$", id_):
        return id_
    import hashlib
    return "tc_" + hashlib.sha256(id_.encode()).hexdigest()[:60]


def _supports_adaptive_thinking(model_id: str) -> bool:
    """Check if model supports adaptive thinking (Opus 4.6+ or Sonnet 4.6+)."""
    return (
        "opus-4-6" in model_id or "opus-4.6" in model_id
        or "sonnet-4-6" in model_id or "sonnet-4.6" in model_id
    )


def _map_thinking_level_to_effort(level: str, model_id: str) -> str:
    """Map thinking level to Anthropic effort string, model-aware."""
    if level == "xhigh":
        # Only Opus 4.6 supports "max"; Sonnet 4.6 tops out at "high"
        return "max" if ("opus-4-6" in model_id or "opus-4.6" in model_id) else "high"
    return {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}.get(level, "high")


def _get_cache_control(base_url: str | None, cache_retention: str | None = None) -> dict | None:
    """
    Build cache_control dict for Anthropic API.
    Uses ephemeral; 1h TTL only for api.anthropic.com and long retention.
    Mirrors getCacheControl() in TypeScript.
    """
    retention = cache_retention or "short"
    if retention == "none":
        return None
    ttl = "1h" if retention == "long" and base_url and "api.anthropic.com" in base_url else None
    result: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        result["ttl"] = ttl
    return result


def _build_client(
    model: Model,
    api_key: str,
    interleaved_thinking: bool = True,
    options_headers: dict[str, str] | None = None,
) -> tuple[_anthropic.AsyncAnthropic, bool]:
    """
    Build the Anthropic async client with appropriate headers.
    Mirrors createClient() in TypeScript.

    Returns (client, is_oauth_token).
    """
    is_oauth = _is_oauth_token(api_key)
    base_url = getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    model_headers = model.headers or {}

    # Adaptive thinking models don't use the interleaved-thinking beta (it's deprecated for them)
    needs_interleaved_beta = interleaved_thinking and not _supports_adaptive_thinking(model.id)

    beta_features = [_BETA_FINE_GRAINED]
    if needs_interleaved_beta:
        beta_features.append(_BETA_INTERLEAVED)

    if is_oauth:
        # OAuth: Bearer auth + Claude Code identity headers
        default_headers = {
            "accept": "application/json",
            "anthropic-beta": f"{_BETA_CLAUDE_CODE},{_BETA_OAUTH},{','.join(beta_features)}",
            "user-agent": f"claude-cli/{_CLAUDE_CODE_VERSION}",
            "x-app": "cli",
            **model_headers,
            **(options_headers or {}),
        }
        client = _anthropic.AsyncAnthropic(
            api_key=None,
            auth_token=api_key,
            base_url=base_url,
            default_headers=default_headers,
        )
    else:
        # Regular API key auth
        default_headers = {
            "accept": "application/json",
            "anthropic-beta": ",".join(beta_features),
            **model_headers,
            **(options_headers or {}),
        }
        client = _anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
        )

    return client, is_oauth


def _convert_tool_result_block(tr_msg: ToolResultMessage, is_oauth: bool = False) -> dict[str, Any]:
    """Convert a single ToolResultMessage to an Anthropic tool_result block."""
    cblocks: list[dict[str, Any]] = []
    for block in tr_msg.content:
        if isinstance(block, TextContent):
            text = sanitize_surrogates(block.text)
            cblocks.append({"type": "text", "text": text})
        elif isinstance(block, ImageContent):
            cblocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.mime_type,
                    "data": block.data,
                },
            })
    return {
        "type": "tool_result",
        "tool_use_id": tr_msg.tool_call_id,
        "content": cblocks,
        "is_error": tr_msg.is_error,
    }


def _build_messages(
    context: Context,
    is_oauth: bool = False,
    cache_control: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Convert Context messages to Anthropic API format.
    Batches consecutive toolResult messages into a single user message.
    """
    result: list[dict[str, Any]] = []
    all_msgs = context.messages
    i = 0

    while i < len(all_msgs):
        msg = all_msgs[i]
        is_last = i == len(all_msgs) - 1

        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                text = _sanitize_surrogates(msg.content)
                if text.strip():
                    block: dict[str, Any] = {"type": "text", "text": text}
                    if is_last and cache_control:
                        block["cache_control"] = cache_control
                    result.append({"role": "user", "content": [block]})
            else:
                content_blocks: list[dict[str, Any]] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            content_blocks.append({"type": "text", "text": text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        })
                if is_last and cache_control and content_blocks:
                    content_blocks[-1] = {**content_blocks[-1], "cache_control": cache_control}
                if content_blocks:
                    result.append({"role": "user", "content": content_blocks})

        elif isinstance(msg, AssistantMessage):
            content_blocks = []
            for block in msg.content:
                if isinstance(block, TextContent):
                    text = sanitize_surrogates(block.text)
                    if text:
                        content_blocks.append({"type": "text", "text": text})
                elif isinstance(block, ThinkingContent):
                    if getattr(block, "redacted", False):
                        # Redacted block: send back as redacted_thinking with opaque data
                        content_blocks.append({
                            "type": "redacted_thinking",
                            "data": block.thinking_signature or "",
                        })
                    elif not block.thinking.strip():
                        pass  # Skip empty thinking blocks
                    else:
                        content_blocks.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.thinking_signature or "",
                        })
                elif isinstance(block, ToolCall):
                    tc_name = _to_claude_code_name(block.name) if is_oauth else block.name
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": tc_name,
                        "input": block.arguments,
                    })
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})

        elif isinstance(msg, ToolResultMessage):
            # Batch consecutive toolResult messages into a single user message
            tool_results: list[dict[str, Any]] = [_convert_tool_result_block(msg, is_oauth)]

            j = i + 1
            while j < len(all_msgs) and isinstance(all_msgs[j], ToolResultMessage):
                tool_results.append(_convert_tool_result_block(all_msgs[j], is_oauth))
                j += 1

            result.append({"role": "user", "content": tool_results})
            i = j
            continue

        i += 1

    return result


def _build_tools(context: Context, is_oauth: bool = False) -> list[dict[str, Any]] | None:
    """Convert Context tools to Anthropic API format, with Claude Code name normalization."""
    if not context.tools:
        return None
    tools = []
    for tool in context.tools:
        name = _to_claude_code_name(tool.name) if is_oauth else tool.name
        tools.append({
            "name": name,
            "description": tool.description,
            "input_schema": tool.parameters,
        })
    return tools


def _build_system(
    context: Context,
    is_oauth: bool,
    cache_control: dict | None,
) -> list[dict[str, Any]] | None:
    """Build system prompt blocks, adding Claude Code identity for OAuth."""
    blocks: list[dict[str, Any]] = []

    if is_oauth:
        # Claude Code identity MUST be first for OAuth
        cc_block: dict[str, Any] = {
            "type": "text",
            "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        }
        if cache_control:
            cc_block["cache_control"] = cache_control
        blocks.append(cc_block)

    if context.system_prompt:
        sp_block: dict[str, Any] = {
            "type": "text",
            "text": _sanitize_surrogates(context.system_prompt),
        }
        if cache_control:
            sp_block["cache_control"] = cache_control
        blocks.append(sp_block)

    return blocks if blocks else None


def _make_empty_assistant(model: Model) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncGenerator[AssistantMessageEvent, None]:
    """
    Stream a response from the Anthropic Messages API.
    Yields AssistantMessageEvents and stores the final AssistantMessage.
    Full parity with TypeScript including OAuth, cache control, beta headers.
    """
    opts = options or SimpleStreamOptions()

    api_key = opts.api_key or ""
    if not api_key:
        # Try to get from environment
        from ..env_api_keys import get_env_api_key
        api_key = get_env_api_key(model.provider) or ""

    is_oauth = _is_oauth_token(api_key)
    base_url = getattr(model, "base_url", None) or getattr(model, "baseUrl", None)
    cache_control = _get_cache_control(base_url, getattr(opts, "cache_retention", None))

    client, is_oauth = _build_client(
        model, api_key,
        interleaved_thinking=True,
        options_headers=getattr(opts, "headers", None),
    )

    # Transform messages for cross-provider compatibility
    transformed_msgs = _transform_messages(context.messages, model, _normalize_tool_call_id)
    transformed_context = Context(
        system_prompt=context.system_prompt,
        messages=transformed_msgs,
        tools=context.tools,
    )

    messages = _build_messages(transformed_context, is_oauth=is_oauth, cache_control=cache_control)
    tools = _build_tools(transformed_context, is_oauth=is_oauth)
    system = _build_system(transformed_context, is_oauth=is_oauth, cache_control=cache_control)

    max_tokens = opts.max_tokens or (model.max_tokens // 3 if model.max_tokens else 4096)

    params: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "max_tokens": max_tokens,
        # Note: "stream": True is NOT passed to client.messages.stream() — the method itself streams
    }

    if system:
        params["system"] = system

    if tools:
        params["tools"] = tools

    # Temperature is incompatible with extended thinking
    if opts.temperature is not None and not opts.reasoning:
        params["temperature"] = opts.temperature

    # Thinking configuration
    if opts.reasoning:
        if _supports_adaptive_thinking(model.id) or is_oauth:
            # Adaptive thinking: effort levels (model-aware)
            effort = _map_thinking_level_to_effort(opts.reasoning, model.id)
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": effort}
        else:
            # Budget-based thinking for older models
            budget = _THINKING_BUDGETS.get(opts.reasoning, 8192)
            if hasattr(opts, "thinking_budgets") and opts.thinking_budgets:
                custom = getattr(opts.thinking_budgets, opts.reasoning, None)
                if custom is not None:
                    budget = custom
            params["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Adjust max_tokens to account for thinking budget
            if budget and max_tokens <= budget:
                params["max_tokens"] = budget + max_tokens

    # Track partial state
    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    block_index_map: dict[int, int] = {}  # anthropic index → content_blocks index
    tool_arg_buffers: dict[int, str] = {}

    # Apply on_payload callback (mirrors TS: const nextParams = await options?.onPayload?.(params, model))
    if opts.get("on_payload"):
        modified = opts["on_payload"](params, model)
        # Support async callbacks
        if hasattr(modified, "__await__"):
            modified = await modified
        if modified is not None:
            params = modified

    yield EventStart(type="start", partial=partial)

    try:
        async with client.messages.stream(**params) as ant_stream:
            async for event in ant_stream:
                event_type = type(event).__name__

                if event_type == "RawMessageStartEvent":
                    # Capture initial token counts from message_start
                    usage_data = getattr(event, "message", {})
                    if hasattr(usage_data, "usage"):
                        u = usage_data.usage
                        partial = partial.model_copy(update={
                            "usage": Usage(
                                input=getattr(u, "input_tokens", 0) or 0,
                                output=getattr(u, "output_tokens", 0) or 0,
                                cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                                cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                            )
                        })

                elif event_type == "RawContentBlockStartEvent":
                    block = event.content_block
                    ant_idx = event.index
                    cb_idx = len(content_blocks)
                    block_index_map[ant_idx] = cb_idx

                    if block.type == "text":
                        content_blocks.append(TextContent(type="text", text=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventTextStart(type="text_start", content_index=cb_idx, partial=partial)

                    elif block.type == "thinking":
                        content_blocks.append(ThinkingContent(type="thinking", thinking=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventThinkingStart(type="thinking_start", content_index=cb_idx, partial=partial)

                    elif block.type == "redacted_thinking":
                        # Opaque encrypted thinking block — preserve signature, no delta events
                        data = getattr(block, "data", "")
                        redacted_block = ThinkingContent(
                            type="thinking",
                            thinking="[Reasoning redacted]",
                            thinking_signature=data,
                            redacted=True,
                        )
                        content_blocks.append(redacted_block)
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventThinkingStart(type="thinking_start", content_index=cb_idx, partial=partial)
                        # Immediately emit end — no delta events for redacted blocks
                        yield EventThinkingEnd(type="thinking_end", content_index=cb_idx, content="[Reasoning redacted]", partial=partial)

                    elif block.type == "tool_use":
                        tc_name = block.name
                        if is_oauth:
                            tc_name = _from_claude_code_name(tc_name, context.tools)
                        tc = ToolCall(
                            type="toolCall",
                            id=block.id,
                            name=tc_name,
                            arguments={},
                        )
                        content_blocks.append(tc)
                        tool_arg_buffers[cb_idx] = ""
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallStart(type="toolcall_start", content_index=cb_idx, partial=partial)

                elif event_type == "RawContentBlockDeltaEvent":
                    delta = event.delta
                    ant_idx = event.index
                    cb_idx = block_index_map.get(ant_idx, -1)
                    if cb_idx < 0 or cb_idx >= len(content_blocks):
                        continue

                    if delta.type == "text_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, TextContent):
                            text = _sanitize_surrogates(delta.text)
                            content_blocks[cb_idx] = TextContent(type="text", text=blk.text + text)
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventTextDelta(type="text_delta", content_index=cb_idx, delta=text, partial=partial)

                    elif delta.type == "thinking_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, ThinkingContent):
                            content_blocks[cb_idx] = ThinkingContent(
                                type="thinking",
                                thinking=blk.thinking + delta.thinking,
                            )
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventThinkingDelta(type="thinking_delta", content_index=cb_idx, delta=delta.thinking, partial=partial)

                    elif delta.type == "input_json_delta":
                        if cb_idx in tool_arg_buffers:
                            tool_arg_buffers[cb_idx] += delta.partial_json
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventToolCallDelta(type="toolcall_delta", content_index=cb_idx, delta=delta.partial_json, partial=partial)

                    elif delta.type == "signature_delta":
                        blk = content_blocks[cb_idx]
                        if isinstance(blk, ThinkingContent):
                            sig = getattr(blk, "thinking_signature", "") or ""
                            content_blocks[cb_idx] = ThinkingContent(
                                type="thinking",
                                thinking=blk.thinking,
                                thinking_signature=sig + delta.signature,
                            )

                elif event_type == "ContentBlockStopEvent":
                    ant_idx = event.index
                    cb_idx = block_index_map.get(ant_idx, -1)
                    if cb_idx < 0 or cb_idx >= len(content_blocks):
                        continue

                    blk = content_blocks[cb_idx]
                    if isinstance(blk, TextContent):
                        yield EventTextEnd(type="text_end", content_index=cb_idx, content=blk.text, partial=partial)
                    elif isinstance(blk, ThinkingContent):
                        yield EventThinkingEnd(type="thinking_end", content_index=cb_idx, content=blk.thinking, partial=partial)
                    elif isinstance(blk, ToolCall):
                        raw = tool_arg_buffers.get(cb_idx, "{}")
                        parsed = parse_streaming_json(raw) or {}
                        content_blocks[cb_idx] = ToolCall(
                            type="toolCall",
                            id=blk.id,
                            name=blk.name,
                            arguments=parsed,
                        )
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallEnd(
                            type="toolcall_end",
                            content_index=cb_idx,
                            tool_call=content_blocks[cb_idx],
                            partial=partial,
                        )

                elif event_type == "RawMessageDeltaEvent":
                    delta = getattr(event, "delta", None)
                    if delta:
                        stop_reason_raw = getattr(delta, "stop_reason", None)
                        if stop_reason_raw:
                            stop_reason = _STOP_REASON_MAP.get(stop_reason_raw, "stop")
                            partial = partial.model_copy(update={"stop_reason": stop_reason})

                    # Update usage if present
                    usage_update = getattr(event, "usage", None)
                    if usage_update:
                        cur = partial.usage
                        inp = getattr(usage_update, "input_tokens", None)
                        out = getattr(usage_update, "output_tokens", None)
                        cr = getattr(usage_update, "cache_read_input_tokens", None)
                        cw = getattr(usage_update, "cache_creation_input_tokens", None)
                        partial = partial.model_copy(update={
                            "usage": Usage(
                                input=inp if inp is not None else cur.input,
                                output=out if out is not None else cur.output,
                                cache_read=cr if cr is not None else cur.cache_read,
                                cache_write=cw if cw is not None else cur.cache_write,
                            )
                        })

            # Get final message from stream
            try:
                final_msg = await ant_stream.get_final_message()
                u = final_msg.usage
                usage = Usage(
                    input=u.input_tokens,
                    output=u.output_tokens,
                    cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                )
                usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write

                stop_reason = _STOP_REASON_MAP.get(final_msg.stop_reason or "end_turn", "stop")
            except Exception:
                usage = partial.usage
                stop_reason = partial.stop_reason

            # Check cancellation
            signal = getattr(opts, "signal", None)
            _is_set = getattr(signal, "is_set", None)
            if signal and callable(_is_set) and _is_set():
                stop_reason = "aborted"

            final = AssistantMessage(
                role="assistant",
                content=content_blocks,
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=usage,
                stop_reason=stop_reason,
                timestamp=int(time.time() * 1000),
            )

            # EventDone only accepts "stop", "length", "toolUse"
            # For "error" or "aborted", emit EventError instead
            if stop_reason in ("error", "aborted"):
                yield EventError(type="error", reason=stop_reason, error=final)
            else:
                yield EventDone(type="done", reason=stop_reason, message=final)

    except Exception as e:
        signal = getattr(opts, "signal", None)
        _is_set_fn = getattr(signal, "is_set", None)
        is_aborted = bool(signal and callable(_is_set_fn) and _is_set_fn())
        stop = "aborted" if is_aborted else "error"

        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason=stop,
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason=stop, error=error_msg)
