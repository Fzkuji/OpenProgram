"""
OpenAI Chat Completions API provider — mirrors packages/ai/src/providers/openai-completions.ts
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

import openai as _openai

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
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ..utils.json_parse import parse_partial_json
from .transform_messages import transform_messages as _transform_messages


def _uses_developer_role(model: Model) -> bool:
    """Check if model uses 'developer' role instead of 'system' (reasoning models)."""
    return bool(getattr(model, "reasoning", False))


def _uses_max_completion_tokens(model: Model) -> bool:
    """Check if model uses max_completion_tokens instead of max_tokens."""
    return bool(getattr(model, "reasoning", False))


def _build_messages(context: Context, model: Model) -> list[dict[str, Any]]:
    """Convert Context messages to OpenAI Chat Completions format."""
    result: list[dict[str, Any]] = []

    if context.system_prompt:
        role = "developer" if _uses_developer_role(model) else "system"
        result.append({"role": role, "content": context.system_prompt})

    for msg in context.messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                result.append({"role": "user", "content": msg.content})
            else:
                content_blocks: list[dict[str, Any]] = []
                for block in msg.content:
                    if isinstance(block, TextContent):
                        content_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{block.mime_type};base64,{block.data}",
                            },
                        })
                result.append({"role": "user", "content": content_blocks})

        elif isinstance(msg, AssistantMessage):
            tool_calls = [c for c in msg.content if isinstance(c, ToolCall)]
            text_parts = [c for c in msg.content if isinstance(c, TextContent)]
            text = " ".join(t.text for t in text_parts) if text_parts else None

            if tool_calls:
                tc_list = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                entry: dict[str, Any] = {"role": "assistant", "tool_calls": tc_list}
                if text:
                    entry["content"] = text
                result.append(entry)
            else:
                result.append({"role": "assistant", "content": text or ""})

        elif isinstance(msg, ToolResultMessage):
            content_text = " ".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            result.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": content_text,
            })

    return result


def _build_tools(context: Context) -> list[dict[str, Any]] | None:
    if not context.tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": False,
            },
        }
        for tool in context.tools
    ]


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
    """Stream a response from the OpenAI Chat Completions API."""
    opts = options or SimpleStreamOptions()

    base_url = model.base_url if model.base_url != "https://api.openai.com/v1" else None
    extra_headers = opts.headers or {}

    client = _openai.AsyncOpenAI(
        api_key=opts.api_key or None,
        base_url=base_url,
        default_headers=extra_headers or None,
    )

    # Transform messages for cross-provider compatibility
    transformed_msgs = _transform_messages(context.messages, model)
    transformed_context = Context(
        system_prompt=context.system_prompt,
        messages=transformed_msgs,
        tools=context.tools,
    )

    messages = _build_messages(transformed_context, model)
    tools = _build_tools(transformed_context)

    params: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    if opts.max_tokens:
        if _uses_max_completion_tokens(model):
            params["max_completion_tokens"] = opts.max_tokens
        else:
            params["max_tokens"] = opts.max_tokens

    if opts.temperature is not None:
        params["temperature"] = opts.temperature

    if tools:
        params["tools"] = tools

    if opts.reasoning:
        effort_map = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}
        params["reasoning_effort"] = effort_map.get(opts.reasoning, "medium")

    partial = _make_empty_assistant(model)
    content_blocks: list[Any] = []
    text_index = -1
    thinking_index = -1
    tool_indices: dict[str, int] = {}
    tool_arg_buffers: dict[str, str] = {}
    usage = Usage()

    yield EventStart(type="start", partial=partial)

    try:
        async with await client.chat.completions.create(**params) as stream:
            async for chunk in stream:
                # Process usage from chunks
                if chunk.usage:
                    u = chunk.usage
                    usage = Usage(
                        input=getattr(u, "prompt_tokens", 0) or 0,
                        output=getattr(u, "completion_tokens", 0) or 0,
                        total_tokens=getattr(u, "total_tokens", 0) or 0,
                    )
                    # Check for reasoning tokens
                    details = getattr(u, "completion_tokens_details", None)
                    if details:
                        reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
                        if reasoning_tokens:
                            usage.output = (getattr(u, "completion_tokens", 0) or 0) - reasoning_tokens

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason

                # Reasoning / thinking content (for o1/o3 models)
                reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning_content:
                    if thinking_index == -1:
                        thinking_index = len(content_blocks)
                        content_blocks.append(ThinkingContent(type="thinking", thinking=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventThinkingStart(type="thinking_start", content_index=thinking_index, partial=partial)

                    content_blocks[thinking_index] = ThinkingContent(
                        type="thinking",
                        thinking=content_blocks[thinking_index].thinking + reasoning_content,
                    )
                    partial = partial.model_copy(update={"content": list(content_blocks)})
                    yield EventThinkingDelta(
                        type="thinking_delta",
                        content_index=thinking_index,
                        delta=reasoning_content,
                        partial=partial,
                    )

                # Text delta
                if delta.content:
                    # Close thinking block if transitioning to text
                    if thinking_index >= 0 and text_index == -1:
                        yield EventThinkingEnd(
                            type="thinking_end",
                            content_index=thinking_index,
                            content=content_blocks[thinking_index].thinking,
                            partial=partial,
                        )

                    if text_index == -1:
                        text_index = len(content_blocks)
                        content_blocks.append(TextContent(type="text", text=""))
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventTextStart(type="text_start", content_index=text_index, partial=partial)

                    content_blocks[text_index] = TextContent(
                        type="text",
                        text=content_blocks[text_index].text + delta.content,
                    )
                    partial = partial.model_copy(update={"content": list(content_blocks)})
                    yield EventTextDelta(
                        type="text_delta",
                        content_index=text_index,
                        delta=delta.content,
                        partial=partial,
                    )

                # Tool call deltas
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        tc_id = tc_delta.id or ""
                        idx_key = str(tc_delta.index)

                        if idx_key not in tool_indices:
                            idx = len(content_blocks)
                            tool_indices[idx_key] = idx
                            tool_arg_buffers[idx_key] = ""
                            content_blocks.append(ToolCall(
                                type="toolCall",
                                id=tc_id or f"call_{idx}",
                                name=tc_delta.function.name or "",
                                arguments={},
                            ))
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventToolCallStart(type="toolcall_start", content_index=idx, partial=partial)

                        if tc_delta.function and tc_delta.function.arguments:
                            tool_arg_buffers[idx_key] += tc_delta.function.arguments
                            partial = partial.model_copy(update={"content": list(content_blocks)})
                            yield EventToolCallDelta(
                                type="toolcall_delta",
                                content_index=tool_indices[idx_key],
                                delta=tc_delta.function.arguments,
                                partial=partial,
                            )

                if finish_reason:
                    # Finalize thinking
                    if thinking_index >= 0 and text_index == -1:
                        yield EventThinkingEnd(
                            type="thinking_end",
                            content_index=thinking_index,
                            content=content_blocks[thinking_index].thinking,
                            partial=partial,
                        )

                    # Finalize text block
                    if text_index >= 0:
                        yield EventTextEnd(
                            type="text_end",
                            content_index=text_index,
                            content=content_blocks[text_index].text,
                            partial=partial,
                        )

                    # Finalize tool calls
                    for idx_key, idx in tool_indices.items():
                        raw = tool_arg_buffers.get(idx_key, "{}")
                        parsed = parse_partial_json(raw) or {}
                        tc = content_blocks[idx]
                        content_blocks[idx] = ToolCall(
                            type="toolCall",
                            id=tc.id,
                            name=tc.name,
                            arguments=parsed,
                        )
                        partial = partial.model_copy(update={"content": list(content_blocks)})
                        yield EventToolCallEnd(
                            type="toolcall_end",
                            content_index=idx,
                            tool_call=content_blocks[idx],
                            partial=partial,
                        )

        # Build final message
        stop_reason_map = {"stop": "stop", "length": "length", "tool_calls": "toolUse"}
        stop_reason = stop_reason_map.get(finish_reason or "", "stop")
        if tool_indices and stop_reason == "stop":
            stop_reason = "toolUse"

        signal = getattr(opts, "signal", None)
        if signal and callable(getattr(signal, "is_set", None)) and signal.is_set():
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

    except _openai.APIError as e:
        error_msg = AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="error",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="error", error=error_msg)
    except Exception as e:
        error_msg = AssistantMessage(
            role="assistant",
            content=content_blocks or [TextContent(type="text", text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=usage,
            stop_reason="error",
            error_message=str(e),
            timestamp=int(time.time() * 1000),
        )
        yield EventError(type="error", reason="error", error=error_msg)
