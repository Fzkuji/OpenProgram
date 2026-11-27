"""OpenAI Chat Completions (``POST /v1/chat/completions``) in and out.

For harnesses that speak only Chat Completions. Reasoning goes out as
``reasoning_content`` (the field DeepSeek and most compatible servers use);
it carries no signature, so earlier reasoning is not sent back upstream.
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from . import conversion as cv
from .conversion import GatewayError
from .responses import error_body  # noqa: F401 - the same OpenAI error object

_FINISH = {"stop": "stop", "length": "length", "toolUse": "tool_calls"}


def _parts(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [TextContent(text=value)] if value else []
    parts: list[Any] = []
    for part in value or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            parts.append(TextContent(text=part["text"]))
        elif part.get("type") == "image_url":
            url = part.get("image_url")
            image = cv.image_from_data_url(url.get("url") if isinstance(url, dict) else url or "")
            if image is not None:
                parts.append(image)
    return parts


def _text(value: Any) -> str:
    return "".join(p.text for p in _parts(value) if isinstance(p, TextContent))


def parse_request(body: Any) -> tuple[Model, Context, SimpleStreamOptions, bool, bool]:
    """``(model, context, options, stream, include_usage)``."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise GatewayError(400, "invalid_request", "messages must be a list")
    model, asked = cv.resolve_model_at(body.get("model"))
    system: list[str] = []
    messages: list[Any] = []
    names: dict[str, str] = {}
    for entry in body["messages"]:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        if role in {"system", "developer"}:
            system.append(_text(entry.get("content")))
        elif role == "user":
            parts = _parts(entry.get("content"))
            if parts:
                messages.append(UserMessage(content=parts, timestamp=cv.now_ms()))
        elif role == "assistant":
            content: list[Any] = []
            text = _text(entry.get("content"))
            if text:
                content.append(TextContent(text=text))
            for call in entry.get("tool_calls") or []:
                function = call.get("function") or {}
                call_id = str(call.get("id") or cv.new_id("call_"))
                names[call_id] = str(function.get("name") or "")
                content.append(ToolCall(id=call_id, name=names[call_id],
                                        arguments=cv.json_arguments(function.get("arguments"))))
            if content:
                messages.append(cv.assistant_message(model, content))
        elif role == "tool":
            call_id = str(entry.get("tool_call_id") or "")
            messages.append(ToolResultMessage(tool_call_id=call_id, tool_name=names.get(call_id, ""),
                                              content=_parts(entry.get("content")) or [TextContent(text="")],
                                              timestamp=cv.now_ms()))
    tools = [Tool(name=t["function"]["name"], description=str(t["function"].get("description") or ""),
                  parameters=t["function"].get("parameters") or {"type": "object", "properties": {}})
             for t in body.get("tools") or []
             if isinstance(t, dict) and t.get("type") == "function" and isinstance(t.get("function"), dict)
             and t["function"].get("name")]
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        choice = {"type": "function", "name": name} if name else None
    elif choice not in {"auto", "required", "none"}:
        choice = None
    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
    effort = asked or body.get("reasoning_effort")
    options = SimpleStreamOptions(
        max_tokens=max_tokens if isinstance(max_tokens, int) else None,
        temperature=body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None,
        tool_choice=choice,
        parallel_tool_calls=body.get("parallel_tool_calls") if isinstance(body.get("parallel_tool_calls"), bool) else None,
        reasoning=cv.reasoning_level(model, effort) if isinstance(effort, str) else None,
        session_id=cv.session_key(body.get("user")),
    )
    context = Context(system_prompt="\n\n".join(s for s in system if s) or None, messages=messages, tools=tools or None)
    stream_options = body.get("stream_options") if isinstance(body.get("stream_options"), dict) else {}
    return model, context, options, bool(body.get("stream")), bool(stream_options.get("include_usage"))


def _usage(message: AssistantMessage | None) -> dict[str, Any]:
    counts = cv.UsageCounts.of(getattr(message, "usage", None))
    prompt = counts.input + counts.cache_read + counts.cache_write
    return {"prompt_tokens": prompt, "completion_tokens": counts.output, "total_tokens": prompt + counts.output,
            "prompt_tokens_details": {"cached_tokens": counts.cache_read}}


def completion_body(model: Model, message: AssistantMessage) -> dict[str, Any]:
    text = "".join(b.text for b in message.content if isinstance(b, TextContent))
    reasoning = "".join(b.thinking for b in message.content if isinstance(b, ThinkingContent) and not b.redacted)
    calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": cv.arguments_json(b)}}
             for b in message.content if isinstance(b, ToolCall)]
    choice_message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if reasoning:
        choice_message["reasoning_content"] = reasoning
    if calls:
        choice_message["tool_calls"] = calls
    return {"id": cv.new_id("chatcmpl-"), "object": "chat.completion", "created": int(time.time()),
            "model": cv.model_ref(model),
            "choices": [{"index": 0, "message": choice_message,
                         "finish_reason": _FINISH.get(message.stop_reason, "stop")}],
            "usage": _usage(message)}


async def stream_events(call, include_usage: bool) -> AsyncIterator[bytes]:
    model: Model = call.model
    chunk_id = cv.new_id("chatcmpl-")
    created = int(time.time())
    tool_index = 0

    def chunk(delta: dict[str, Any], finish: str | None = None, usage: dict | None = None,
              choices: bool = True) -> bytes:
        payload: dict[str, Any] = {"id": chunk_id, "object": "chat.completion.chunk", "created": created,
                                   "model": cv.model_ref(model),
                                   "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if choices else []}
        if usage is not None:
            payload["usage"] = usage
        return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")

    yield chunk({"role": "assistant", "content": ""})
    try:
        async for event in call.events():
            kind = getattr(event, "type", None)
            if kind == "text_delta" and event.delta:
                yield chunk({"content": event.delta})
            elif kind == "thinking_delta" and event.delta:
                yield chunk({"reasoning_content": event.delta})
            elif kind == "toolcall_end":
                tool = event.tool_call
                yield chunk({"tool_calls": [{"index": tool_index, "id": tool.id or cv.new_id("call_"), "type": "function",
                                             "function": {"name": tool.name, "arguments": cv.arguments_json(tool)}}]})
                tool_index += 1
            elif kind == "done":
                yield chunk({}, _FINISH.get(event.message.stop_reason, "stop"))
                if include_usage:
                    yield chunk({}, usage=_usage(event.message), choices=False)
    except GatewayError as error:
        yield f"data: {json.dumps(error_body(error), ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"
