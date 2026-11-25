"""OpenAI Responses (``POST /v1/responses``) in and out, for Codex.

The gateway keeps no responses: a request carries its whole input (Codex
sends ``store: false``), so ``previous_response_id`` is refused. Codex's
freeform ``custom`` tools (``apply_patch``) reach the model as a function
with one string argument, ``input``, and come back as ``custom_tool_call``.
Reasoning comes back as a ``reasoning`` item whose ``encrypted_content``
carries the model's own signature, wrapped (``conversion.wrap_signature``).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
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

_CODES = {
    "context_length": "context_length_exceeded",
    "rate_limit": "rate_limit_exceeded",
    "invalid_request": "invalid_request",
    "not_found": "model_not_found",
    "authentication": "invalid_api_key",
    "permission": "permission_denied",
    "overloaded": "server_is_overloaded",
    "api_error": "server_error",
}
_TYPES = {
    "context_length": "invalid_request_error",
    "invalid_request": "invalid_request_error",
    "not_found": "invalid_request_error",
    "authentication": "authentication_error",
    "permission": "permission_error",
    "rate_limit": "rate_limit_error",
}


def error_body(error: GatewayError) -> dict[str, Any]:
    """OpenAI's error object (Responses and Chat Completions share it)."""
    return {"error": {"message": error.message, "type": _TYPES.get(error.kind, "server_error"),
                      "param": None, "code": _CODES.get(error.kind, "server_error")}}


@dataclass
class ResponsesRequest:
    model: Model
    context: Context
    options: SimpleStreamOptions
    stream: bool
    custom_tools: set[str] = field(default_factory=set)


# Request

def _content_parts(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [TextContent(text=value)] if value else []
    parts: list[Any] = []
    for part in value or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text"} and part.get("text"):
            parts.append(TextContent(text=part["text"]))
        elif kind == "input_image":
            url = part.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            image = cv.image_from_data_url(url or "")
            if image is not None:
                parts.append(image)
        elif kind == "input_file":
            parts.append(TextContent(text=f"[{part.get('filename') or 'file'} not passed to this model]"))
    return parts


def _input(model: Model, raw: Any, system: list[str], custom: set[str]) -> list[Any]:
    if isinstance(raw, str):
        return [UserMessage(content=raw, timestamp=cv.now_ms())]
    if not isinstance(raw, list):
        raise GatewayError(400, "invalid_request", "input must be a string or a list")
    out: list[Any] = []
    pending: list[Any] = []
    names: dict[str, str] = {}

    def flush() -> None:
        if pending:
            out.append(cv.assistant_message(model, list(pending)))
            pending.clear()

    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = item.get("role")
            parts = _content_parts(item.get("content"))
            if role in {"system", "developer"}:
                system.extend(p.text for p in parts if isinstance(p, TextContent))
            elif role == "assistant":
                pending.extend(p for p in parts if isinstance(p, TextContent))
            elif parts:
                flush()
                out.append(UserMessage(content=parts, timestamp=cv.now_ms()))
        elif kind == "reasoning":
            summary = "\n\n".join(s.get("text", "") for s in item.get("summary") or [] if isinstance(s, dict))
            thinking = cv.thinking_for(model, summary, item.get("encrypted_content"), raw_is_anthropic=False)
            if thinking is not None:
                pending.append(thinking)
        elif kind in {"function_call", "custom_tool_call"}:
            call_id = str(item.get("call_id") or item.get("id") or cv.new_id("call_"))
            name = str(item.get("name") or "")
            names[call_id] = name
            if kind == "custom_tool_call":
                arguments = {"input": item.get("input") or ""}
            else:
                arguments = cv.json_arguments(item.get("arguments"))
            pending.append(ToolCall(id=call_id, name=name, arguments=arguments))
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            flush()
            call_id = str(item.get("call_id") or "")
            output = item.get("output")
            if isinstance(output, dict):  # {"content": ..., "success": ...} from some clients
                output = output.get("content") or json.dumps(output, ensure_ascii=False)
            out.append(ToolResultMessage(tool_call_id=call_id, tool_name=names.get(call_id, ""),
                                         content=_content_parts(output) or [TextContent(text="")],
                                         timestamp=cv.now_ms()))
    flush()
    return out


def _custom_parameters(tool: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    description = str(tool.get("description") or "")
    fmt = tool.get("format") if isinstance(tool.get("format"), dict) else {}
    if fmt.get("type") == "grammar" and fmt.get("definition"):
        description += (f"\n\nPass the whole input as the `input` string. It follows this "
                        f"{fmt.get('syntax') or 'grammar'}:\n{fmt['definition']}")
    return description, {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"],
                         "additionalProperties": False}


def _tools(raw: Any, custom: set[str]) -> list[Tool] | None:
    tools = []
    for tool in raw or []:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type")
        if kind == "function" and tool.get("name"):
            tools.append(Tool(name=tool["name"], description=str(tool.get("description") or ""),
                              parameters=tool.get("parameters") or {"type": "object", "properties": {}}))
        elif kind == "custom" and tool.get("name"):
            description, parameters = _custom_parameters(tool)
            custom.add(tool["name"])
            tools.append(Tool(name=tool["name"], description=description, parameters=parameters))
    return tools or None


def _tool_choice(value: Any) -> Any:
    if value in {"auto", "required", "none"}:
        return value
    if isinstance(value, dict) and value.get("name"):
        return {"type": "function", "name": value["name"]}
    return None


def parse_request(body: Any) -> ResponsesRequest:
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "the request body must be a JSON object")
    if body.get("previous_response_id"):
        raise GatewayError(400, "invalid_request",
                           "previous_response_id is not supported here; send the whole input")
    model = cv.resolve_model(body.get("model"))
    system: list[str] = [body["instructions"]] if isinstance(body.get("instructions"), str) and body["instructions"] else []
    custom: set[str] = set()
    tools = _tools(body.get("tools"), custom)
    messages = _input(model, body.get("input", []), system, custom)
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
    effort = reasoning.get("effort")
    options = SimpleStreamOptions(
        max_tokens=body.get("max_output_tokens") if isinstance(body.get("max_output_tokens"), int) else None,
        temperature=body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None,
        tool_choice=_tool_choice(body.get("tool_choice")),
        parallel_tool_calls=body.get("parallel_tool_calls") if isinstance(body.get("parallel_tool_calls"), bool) else None,
        reasoning=cv.reasoning_level(model, effort) if isinstance(effort, str) and effort != "none" else None,
        session_id=cv.session_key(body.get("prompt_cache_key")),
    )
    context = Context(system_prompt="\n\n".join(system) or None, messages=messages, tools=tools)
    return ResponsesRequest(model, context, options, bool(body.get("stream")), custom)


# Reply

def _usage(message: AssistantMessage | None) -> dict[str, Any]:
    counts = cv.UsageCounts.of(getattr(message, "usage", None))
    total_in = counts.input + counts.cache_read + counts.cache_write
    return {"input_tokens": total_in, "input_tokens_details": {"cached_tokens": counts.cache_read},
            "output_tokens": counts.output, "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": total_in + counts.output}


def _reasoning_item(model: Model, item_id: str, block: ThinkingContent) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "reasoning", "id": item_id,
                            "summary": [{"type": "summary_text", "text": block.thinking}]
                            if block.thinking and not block.redacted else []}
    signature = cv.client_signature(model, block.thinking_signature, native_anthropic=False)
    if signature:
        item["encrypted_content"] = signature
    return item


def _call_item(call: ToolCall, item_id: str, custom: set[str]) -> dict[str, Any]:
    call_id = call.id or cv.new_id("call_")
    if call.name in custom:
        value = (call.arguments or {}).get("input")
        return {"type": "custom_tool_call", "id": item_id, "call_id": call_id, "name": call.name,
                "input": value if isinstance(value, str) else json.dumps(call.arguments or {}, ensure_ascii=False),
                "status": "completed"}
    return {"type": "function_call", "id": item_id, "call_id": call_id, "name": call.name,
            "arguments": cv.arguments_json(call), "status": "completed"}


def _message_item(item_id: str, text: str) -> dict[str, Any]:
    return {"type": "message", "id": item_id, "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _response(response_id: str, model: Model, created: int, status: str,
              output: list[dict[str, Any]], message: AssistantMessage | None = None,
              error: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"id": response_id, "object": "response", "created_at": created,
                            "status": status, "model": cv.model_ref(model), "output": output,
                            "error": error, "incomplete_details": None}
    if status == "incomplete":
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    if message is not None:
        body["usage"] = _usage(message)
    return body


def response_body(request: ResponsesRequest, message: AssistantMessage) -> dict[str, Any]:
    """A whole reply as a Responses object."""
    output = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text:
            output.append(_message_item(cv.new_id("msg_"), block.text))
        elif isinstance(block, ThinkingContent):
            output.append(_reasoning_item(request.model, cv.new_id("rs_"), block))
        elif isinstance(block, ToolCall):
            output.append(_call_item(block, cv.new_id("fc_"), request.custom_tools))
    status = "incomplete" if message.stop_reason == "length" else "completed"
    return _response(cv.new_id("resp_"), request.model, int(time.time()), status, output, message)


async def stream_events(call, request: ResponsesRequest) -> AsyncIterator[bytes]:
    """The provider's events as Responses server-sent events."""
    model: Model = call.model
    response_id = cv.new_id("resp_")
    created = int(time.time())
    sequence = 0
    output: list[dict[str, Any]] = []
    open_item: dict[str, Any] | None = None  # {"kind", "at", "id", "index", "text"}

    def sse(kind: str, payload: dict[str, Any]) -> bytes:
        nonlocal sequence
        payload = {"type": kind, "sequence_number": sequence, **payload}
        sequence += 1
        return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")

    def close(block: Any = None) -> list[bytes]:
        nonlocal open_item
        if open_item is None:
            return []
        item, open_item = open_item, None
        at, item_id, text = item["index"], item["id"], item["text"]
        if item["kind"] == "text":
            done = _message_item(item_id, text)
            out = [sse("response.output_text.done", {"item_id": item_id, "output_index": at, "content_index": 0, "text": text}),
                   sse("response.content_part.done", {"item_id": item_id, "output_index": at, "content_index": 0,
                                                      "part": done["content"][0]})]
        else:
            thinking = block if isinstance(block, ThinkingContent) else ThinkingContent(thinking=text)
            done = _reasoning_item(model, item_id, thinking)
            done["summary"] = [{"type": "summary_text", "text": text}] if text else []
            out = [sse("response.reasoning_summary_text.done", {"item_id": item_id, "output_index": at, "summary_index": 0, "text": text}),
                   sse("response.reasoning_summary_part.done", {"item_id": item_id, "output_index": at, "summary_index": 0,
                                                                "part": {"type": "summary_text", "text": text}})]
        output.append(done)
        return out + [sse("response.output_item.done", {"output_index": at, "item": done})]

    def open_(kind: str, at: int) -> list[bytes]:
        nonlocal open_item
        out = close()
        item_id = cv.new_id("msg_" if kind == "text" else "rs_")
        index = len(output)
        open_item = {"kind": kind, "at": at, "id": item_id, "index": index, "text": ""}
        if kind == "text":
            added = {"type": "message", "id": item_id, "role": "assistant", "status": "in_progress", "content": []}
            return out + [sse("response.output_item.added", {"output_index": index, "item": added}),
                          sse("response.content_part.added", {"item_id": item_id, "output_index": index, "content_index": 0,
                                                              "part": {"type": "output_text", "text": "", "annotations": []}})]
        added = {"type": "reasoning", "id": item_id, "summary": []}
        return out + [sse("response.output_item.added", {"output_index": index, "item": added}),
                      sse("response.reasoning_summary_part.added", {"item_id": item_id, "output_index": index, "summary_index": 0,
                                                                    "part": {"type": "summary_text", "text": ""}})]

    yield sse("response.created", {"response": _response(response_id, model, created, "in_progress", [])})
    yield sse("response.in_progress", {"response": _response(response_id, model, created, "in_progress", [])})
    try:
        async for event in call.events():
            kind = getattr(event, "type", None)
            out: list[bytes] = []
            if kind == "text_delta" and event.delta:
                if not (open_item and open_item["kind"] == "text" and open_item["at"] == event.content_index):
                    out += open_("text", event.content_index)
                open_item["text"] += event.delta
                out.append(sse("response.output_text.delta", {"item_id": open_item["id"], "output_index": open_item["index"],
                                                              "content_index": 0, "delta": event.delta}))
            elif kind == "text_end" and open_item and open_item["kind"] == "text" and open_item["at"] == event.content_index:
                out += close()
            elif kind == "thinking_delta" and event.delta:
                if not (open_item and open_item["kind"] == "thinking" and open_item["at"] == event.content_index):
                    out += open_("thinking", event.content_index)
                open_item["text"] += event.delta
                out.append(sse("response.reasoning_summary_text.delta", {"item_id": open_item["id"], "output_index": open_item["index"],
                                                                         "summary_index": 0, "delta": event.delta}))
            elif kind == "thinking_end":
                block = event.partial.content[event.content_index] if event.content_index < len(event.partial.content) else None
                if open_item and open_item["kind"] == "thinking" and open_item["at"] == event.content_index:
                    out += close(block)
                elif isinstance(block, ThinkingContent) and block.thinking_signature:
                    out += close()
                    item = _reasoning_item(model, cv.new_id("rs_"), block)
                    out.append(sse("response.output_item.added", {"output_index": len(output), "item": {**item, "summary": []}}))
                    out.append(sse("response.output_item.done", {"output_index": len(output), "item": item}))
                    output.append(item)
            elif kind == "toolcall_end":
                out += close()
                at = len(output)
                item = _call_item(event.tool_call, cv.new_id("fc_" if event.tool_call.name not in request.custom_tools else "ctc_"),
                                  request.custom_tools)
                if item["type"] == "function_call":
                    out.append(sse("response.output_item.added", {"output_index": at, "item": {**item, "arguments": "", "status": "in_progress"}}))
                    out.append(sse("response.function_call_arguments.delta", {"item_id": item["id"], "output_index": at, "delta": item["arguments"]}))
                    out.append(sse("response.function_call_arguments.done", {"item_id": item["id"], "output_index": at, "arguments": item["arguments"]}))
                else:
                    out.append(sse("response.output_item.added", {"output_index": at, "item": {**item, "input": "", "status": "in_progress"}}))
                out.append(sse("response.output_item.done", {"output_index": at, "item": item}))
                output.append(item)
            elif kind == "done":
                out += close()
                message = event.message
                status = "incomplete" if message.stop_reason == "length" else "completed"
                out.append(sse(f"response.{status}", {"response": _response(response_id, model, created, status, output, message)}))
            for chunk in out:
                yield chunk
    except GatewayError as error:
        detail = error_body(error)["error"]
        yield sse("response.failed", {"response": _response(response_id, model, created, "failed", output,
                                                           error={"code": detail["code"], "message": detail["message"]})})
