"""Anthropic Messages (``POST /v1/messages``) in and out, for Claude Code.

:func:`parse_request` reads a Messages request into OpenProgram's context,
:func:`stream_events` writes the provider's events as Messages server-sent
events (``message_start``, ``content_block_start/delta/stop`` for text,
thinking and ``tool_use`` with ``input_json_delta``, ``message_delta`` with
the stop reason and usage, ``message_stop``) and :func:`message_body` writes
a whole reply. ``count_tokens`` is estimated locally. Anthropic's web search
tool is told as :mod:`.web_search` says.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    ServerToolUse,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from . import conversion as cv
from . import web_search
from .conversion import GatewayError

_STOP = {"stop": "end_turn", "length": "max_tokens", "toolUse": "tool_use"}
_ERROR_TYPES = {
    "invalid_request": "invalid_request_error",
    "context_length": "invalid_request_error",
    "authentication": "authentication_error",
    "permission": "permission_error",
    "not_found": "not_found_error",
    "rate_limit": "rate_limit_error",
    "overloaded": "overloaded_error",
    "api_error": "api_error",
}


def error_body(error: GatewayError) -> dict[str, Any]:
    message = error.message
    if error.kind == "context_length" and "prompt is too long" not in message:
        # Claude Code compacts on this wording.
        message = f"prompt is too long: {message}"
    return {"type": "error", "error": {"type": _ERROR_TYPES.get(error.kind, "api_error"), "message": message}}


# Request

def _system(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        parts = [b.get("text", "") for b in value if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n\n".join(p for p in parts if p)
        return joined or None
    return None


def _image(block: dict[str, Any]) -> ImageContent | None:
    source = block.get("source") or {}
    if source.get("type") == "base64" and source.get("data"):
        return ImageContent(data=source["data"], mime_type=source.get("media_type") or "image/png",
                            cache_control=block.get("cache_control") if isinstance(block.get("cache_control"), dict) else None)
    return None


def _result_content(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [TextContent(text=value)]
    blocks: list[Any] = []
    for block in value or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            blocks.append(cv.text_block(block.get("text", ""), block.get("cache_control")))
        elif block.get("type") == "image":
            image = _image(block)
            if image is not None:
                blocks.append(image)
    return blocks


def _messages(model: Model, raw: list[Any]) -> list[Any]:
    out: list[Any] = []
    names: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise GatewayError(400, "invalid_request", "each message must be an object")
        role, content = entry.get("role"), entry.get("content")
        if role == "user":
            if isinstance(content, str):
                out.append(UserMessage(content=content, timestamp=cv.now_ms()))
                continue
            results: list[ToolResultMessage] = []
            blocks: list[Any] = []
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_result":
                    call_id = str(block.get("tool_use_id") or "")
                    results.append(ToolResultMessage(
                        tool_call_id=call_id, tool_name=names.get(call_id, ""),
                        content=_result_content(block.get("content")),
                        is_error=bool(block.get("is_error")), timestamp=cv.now_ms()))
                elif kind == "text":
                    blocks.append(cv.text_block(block.get("text", ""), block.get("cache_control")))
                elif kind == "image":
                    image = _image(block)
                    if image is not None:
                        blocks.append(image)
                elif kind == "document":
                    title = block.get("title") or "document"
                    blocks.append(TextContent(text=f"[{title} not passed to this model]"))
            out.extend(results)
            if blocks:
                out.append(UserMessage(content=blocks, timestamp=cv.now_ms()))
        elif role == "assistant":
            parts: list[Any] = []
            if isinstance(content, str):
                if content:
                    parts.append(TextContent(text=content))
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    parts.append(TextContent(text=block["text"]))
                elif kind == "thinking":
                    thinking = cv.thinking_for(model, block.get("thinking", ""), block.get("signature"),
                                               raw_is_anthropic=True)
                    if thinking is not None:
                        parts.append(thinking)
                elif kind == "redacted_thinking":
                    thinking = cv.thinking_for(model, "[Reasoning redacted]", block.get("data"),
                                               redacted=True, raw_is_anthropic=True)
                    if thinking is not None:
                        parts.append(thinking)
                elif kind == "tool_use":
                    call_id = str(block.get("id") or cv.new_id("toolu_"))
                    names[call_id] = str(block.get("name") or "")
                    parts.append(ToolCall(id=call_id, name=names[call_id],
                                          arguments=cv.json_arguments(block.get("input"))))
            if parts:
                out.append(cv.assistant_message(model, parts))
        else:
            raise GatewayError(400, "invalid_request", f"unknown message role {role!r}")
    return out


def _tools(raw: Any) -> list[Tool] | None:
    tools = []
    for tool in raw or []:
        if not isinstance(tool, dict) or "input_schema" not in tool:
            continue  # a server tool (web search, code execution) the model does not run here
        if tool.get("type") not in (None, "custom"):
            continue
        tools.append(Tool(name=str(tool.get("name") or ""), description=str(tool.get("description") or ""),
                          parameters=tool.get("input_schema") or {"type": "object", "properties": {}},
                          cache_control=tool.get("cache_control") if isinstance(tool.get("cache_control"), dict) else None))
    return tools or None


def server_search(raw: Any) -> str:
    """The name of Anthropic's web search server tool (``web_search_…``) among
    a request's tools, ``""`` for none."""
    for tool in raw if isinstance(raw, list) else []:
        if isinstance(tool, dict) and str(tool.get("type") or "").startswith("web_search_"):
            return str(tool.get("name") or "web_search")
    return ""


def _tool_choice(value: Any) -> tuple[Any, bool | None]:
    if not isinstance(value, dict):
        return None, None
    parallel = False if value.get("disable_parallel_tool_use") else None
    kind = value.get("type")
    if kind == "any":
        return "required", parallel
    if kind == "tool" and value.get("name"):
        return {"type": "function", "name": value["name"]}, parallel
    if kind == "none":
        return "none", parallel
    return ("auto" if kind == "auto" else None), parallel


def _reasoning(model: Model, body: dict[str, Any], asked: str = "") -> str | None:
    if asked:
        return cv.reasoning_level(model, asked)
    effort = (body.get("output_config") or {}).get("effort") if isinstance(body.get("output_config"), dict) else None
    thinking = body.get("thinking") if isinstance(body.get("thinking"), dict) else {}
    kind = thinking.get("type")
    if kind == "disabled":
        return None
    if isinstance(effort, str):
        return cv.reasoning_level(model, effort)
    if kind == "enabled":
        return cv.reasoning_level(model, cv.budget_level(thinking.get("budget_tokens")))
    if kind == "adaptive":
        return cv.reasoning_level(model, model.default_thinking_level or "medium")
    return None


def parse_request(body: Any) -> tuple[Model, Context, SimpleStreamOptions, bool]:
    """``(model, context, options, stream)`` of a Messages request."""
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "the request body must be a JSON object")
    if not isinstance(body.get("messages"), list):
        raise GatewayError(400, "invalid_request", "messages must be a list")
    model, asked = cv.resolve_model_at(body.get("model"))
    context = Context(system_prompt=_system(body.get("system")),
                      messages=_messages(model, body["messages"]), tools=_tools(body.get("tools")))
    choice, parallel = _tool_choice(body.get("tool_choice"))
    search = server_search(body.get("tools"))
    if search and isinstance(choice, dict) and choice.get("name") == search and not any(
            tool.name == search for tool in context.tools or []):
        choice = None  # the server tool asked for is the provider's search, no function
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    options = SimpleStreamOptions(
        max_tokens=body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else None,
        temperature=body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None,
        tool_choice=choice, parallel_tool_calls=parallel,
        reasoning=_reasoning(model, body, asked), session_id=cv.session_key(metadata.get("user_id")),
        # Anthropic's web_search server tool (ZCode's native web search,
        # Claude Code's WebSearch): the provider searches, where it can
        web_search=True if search and cv.searches(model) else None,
    )
    return model, context, options, bool(body.get("stream"))


def count_tokens(body: Any) -> dict[str, int]:
    """A local estimate of a request's input tokens."""
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "the request body must be a JSON object")
    texts: list[str] = []
    images = 0

    def walk(value: Any) -> None:
        nonlocal images
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if value.get("type") == "image":
                images += 1
                return
            for key, item in value.items():
                if key not in {"type", "cache_control", "id", "tool_use_id", "signature", "data"}:
                    walk(item)

    walk(body.get("system"))
    walk(body.get("messages"))
    if body.get("tools"):
        texts.append(json.dumps(body["tools"], ensure_ascii=False))
    return {"input_tokens": cv.estimate_tokens(texts, images)}


# Reply

def _sse(kind: str, payload: dict[str, Any]) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")


def _usage(message: AssistantMessage | None) -> dict[str, int]:
    counts = cv.UsageCounts.of(getattr(message, "usage", None))
    return {"input_tokens": counts.input, "output_tokens": counts.output,
            "cache_read_input_tokens": counts.cache_read, "cache_creation_input_tokens": counts.cache_write}


def _thinking_block(model: Model, block: ThinkingContent) -> dict[str, Any]:
    signature = cv.client_signature(model, block.thinking_signature, native_anthropic=True)
    if block.redacted:
        return {"type": "redacted_thinking", "data": signature or ""}
    return {"type": "thinking", "thinking": block.thinking, "signature": signature or ""}


def message_body(model: Model, message: AssistantMessage, message_id: str | None = None) -> dict[str, Any]:
    """A whole reply as a Messages response object."""
    content: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            if block.redacted and not block.thinking_signature:
                continue
            content.append(_thinking_block(model, block))
        elif isinstance(block, ToolCall):
            content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments or {}})
        elif web_search.renders(block):
            content.append({"type": "server_tool_use", "id": web_search.server_id(block), "name": "web_search",
                            "input": web_search.query_of(block)})
            content.append(web_search.result_block(block))
    return {"id": message_id or cv.new_id("msg_"), "type": "message", "role": "assistant",
            "model": cv.model_ref(model), "content": content,
            "stop_reason": _STOP.get(message.stop_reason, "end_turn"), "stop_sequence": None,
            "usage": _usage(message)}


async def stream_events(call) -> AsyncIterator[bytes]:
    """The provider's events as Messages server-sent events."""
    model: Model = call.model
    message_id = cv.new_id("msg_")
    started = False
    index = -1
    open_block: tuple[str, int] | None = None  # (kind, provider content index)

    def start_message() -> list[bytes]:
        nonlocal started
        if started:
            return []
        started = True
        return [_sse("message_start", {"type": "message_start", "message": {
            "id": message_id, "type": "message", "role": "assistant", "model": cv.model_ref(model),
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}})]

    def close() -> list[bytes]:
        nonlocal open_block
        if open_block is None:
            return []
        open_block = None
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": index})]

    def open_(kind: str, at: int, block: dict[str, Any]) -> list[bytes]:
        nonlocal index, open_block
        out = start_message() + close()
        index += 1
        open_block = (kind, at)
        return out + [_sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})]

    def delta(payload: dict[str, Any]) -> bytes:
        return _sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": payload})

    try:
        async for event in call.events():
            kind = getattr(event, "type", None)
            out: list[bytes] = []
            if kind == "text_delta" and event.delta:
                if open_block != ("text", event.content_index):
                    out += open_("text", event.content_index, {"type": "text", "text": ""})
                out.append(delta({"type": "text_delta", "text": event.delta}))
            elif kind == "text_end" and open_block == ("text", event.content_index):
                out += close()
            elif kind == "thinking_delta" and event.delta:
                if open_block != ("thinking", event.content_index):
                    out += open_("thinking", event.content_index, {"type": "thinking", "thinking": "", "signature": ""})
                out.append(delta({"type": "thinking_delta", "thinking": event.delta}))
            elif kind == "thinking_end":
                block = event.partial.content[event.content_index] if event.content_index < len(event.partial.content) else None
                if isinstance(block, ThinkingContent):
                    rendered = _thinking_block(model, block)
                    if open_block == ("thinking", event.content_index):
                        if rendered.get("signature"):
                            out.append(delta({"type": "signature_delta", "signature": rendered["signature"]}))
                        out += close()
                    elif block.redacted and rendered.get("data"):
                        out += open_("redacted", event.content_index, rendered) + close()
            elif kind == "toolcall_end":
                call_block = event.tool_call
                out += open_("tool", event.content_index, {"type": "tool_use", "id": call_block.id or cv.new_id("toolu_"),
                                                           "name": call_block.name, "input": {}})
                out.append(delta({"type": "input_json_delta", "partial_json": cv.arguments_json(call_block)}))
                out += close()
            elif kind == "servertool_end" and web_search.renders(event.server_tool):
                found: ServerToolUse = event.server_tool
                # as Anthropic's own web search tells it: the search, then its pages
                out += open_("server", event.content_index, {"type": "server_tool_use", "id": web_search.server_id(found),
                                                             "name": "web_search", "input": {}})
                out.append(delta({"type": "input_json_delta",
                                  "partial_json": json.dumps(web_search.query_of(found), ensure_ascii=False)}))
                out += open_("server_result", event.content_index, web_search.result_block(found)) + close()
            elif kind == "done":
                message = event.message
                out += start_message() + close()
                out.append(_sse("message_delta", {"type": "message_delta",
                                                  "delta": {"stop_reason": _STOP.get(message.stop_reason, "end_turn"),
                                                            "stop_sequence": None},
                                                  "usage": _usage(message)}))
                out.append(_sse("message_stop", {"type": "message_stop"}))
            for chunk in out:
                yield chunk
    except GatewayError as error:
        yield _sse("error", error_body(error))
