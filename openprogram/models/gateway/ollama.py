"""The Ollama API, for clients that take a custom endpoint only as an Ollama server.

Ported from yetone/magpie ``internal/gateway/ollama.go`` and
``ollama_chat.go`` (MIT License, Copyright (c) yetone). VS Code's Copilot
"Ollama" provider asks ``/api/version`` (refusing a server older than
0.6.4), lists ``/api/tags``, asks ``/api/show`` of each model (one with no
capabilities is left out) and chats on ``/v1/chat/completions`` under the
same base; Visual Studio's Copilot and others chat on ``/api/chat``.

The routes sit under ``/gateway/ollama`` (the app's own ``/api`` holds the
server root), so a client is given ``http://127.0.0.1:<port>/gateway/ollama``
as its Ollama endpoint. ``/api/chat`` and ``/api/generate`` are read as a
Chat Completions request and answered as NDJSON lines, the last one with
``"done": true``, its reason and token counts; ``"stream": false`` answers
one object. Images are bare base64, tool-call arguments are objects.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from openprogram.models.providers.types import AssistantMessage, TextContent, ThinkingContent, ToolCall

from . import conversion as cv
from .conversion import GatewayError

VERSION = "0.12.6"
ARCH = "openprogram"
DEFAULT_CONTEXT = 32768
MODIFIED = "2025-01-01T00:00:00Z"


def error_body(error: GatewayError) -> dict[str, Any]:
    return {"error": error.message}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(model_id: str) -> str:
    return hashlib.sha256(model_id.encode("utf-8")).hexdigest()


def _details(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"parent_model": "", "format": "", "family": ARCH, "families": [ARCH],
                           "parameter_size": "", "quantization_level": ""}
    if row.get("context_window"):
        out["context_length"] = int(row["context_window"])
    return out


def _capabilities(row: dict[str, Any]) -> list[str]:
    caps = ["completion", "tools"]
    if row.get("vision"):
        caps.append("vision")
    if row.get("reasoning") or [lvl for lvl in row.get("thinking_levels") or [] if lvl != "off"]:
        caps.append("thinking")
    return caps


def _rows() -> list[dict[str, Any]]:
    return cv.enabled_model_refs()


def entry(name: str) -> Optional[dict[str, Any]]:
    """The listed model ``name`` names; the ollama CLI's ``:latest`` comes off."""
    rows = _rows()
    wanted = name[len(cv.REF_PREFIX):] if name.startswith(cv.REF_PREFIX) else name
    for candidate in (wanted, wanted[:-len(":latest")] if wanted.endswith(":latest") else None):
        for row in rows:
            if candidate and row["id"] == candidate:
                return row
    return None


def model_name(name: str) -> str:
    row = entry(name)
    return row["id"] if row else name


def version() -> dict[str, Any]:
    return {"version": VERSION}


def tags() -> dict[str, Any]:
    return {"models": [{"name": row["id"], "model": row["id"], "modified_at": MODIFIED, "size": 0,
                        "digest": _digest(row["id"]), "details": _details(row), "capabilities": _capabilities(row)}
                       for row in _rows()]}


def show(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "invalid request: the body must be a JSON object")
    name = str(body.get("model") or "").strip() or str(body.get("name") or "").strip()
    if not name:
        raise GatewayError(400, "invalid_request", "model is required")
    row = entry(name)
    if row is None:
        raise GatewayError(404, "not_found", f"model '{name}' not found")
    window = int(row.get("context_window") or 0) or DEFAULT_CONTEXT
    info = {"general.architecture": ARCH, "general.basename": row.get("name") or row["id"],
            "general.parameter_count": 0, f"{ARCH}.context_length": window, f"{ARCH}.embedding_length": 0}
    return {"modelfile": "", "parameters": "", "template": "", "details": _details(row),
            "model_info": info, "capabilities": _capabilities(row), "modified_at": MODIFIED}


# Chat

def _image_type(data: str) -> str:
    head = data[:64]
    try:
        raw = base64.b64decode(head[:len(head) // 4 * 4])
    except ValueError:
        return "image/png"
    for magic, mime in ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
                        (b"RIFF", "image/webp"), (b"BM", "image/bmp")):
        if raw.startswith(magic):
            return mime
    return "image/png"


def _content(text: str, images: list[Any] | None) -> Any:
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for image in images:
        image = str(image)
        url = image if image.startswith("data:") else f"data:{_image_type(image)};base64,{image}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _messages(raw: list[Any]) -> list[dict[str, Any]]:
    """Ollama's messages in Chat Completions' shape: a call with no id gets
    one, a result is matched by the id it names, else the tool's name, else
    in order."""
    open_calls: list[tuple[str, str]] = []
    out: list[dict[str, Any]] = []
    for index, message in enumerate(raw):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        text = str(message.get("content") or "")
        if role in {"system", "developer"}:
            out.append({"role": role, "content": text})
        elif role == "user":
            out.append({"role": "user", "content": _content(text, message.get("images"))})
        elif role == "assistant":
            entry_: dict[str, Any] = {"role": "assistant", "content": text}
            if message.get("thinking"):
                entry_["reasoning_content"] = message["thinking"]
            calls = message.get("tool_calls")
            calls = [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
            if calls:
                open_calls = []
                written = []
                for position, call in enumerate(calls):
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    call_id = str(call.get("id") or f"call_{index}_{position}")
                    arguments = function.get("arguments")
                    if arguments in (None, ""):
                        arguments = "{}"
                    elif not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    written.append({"id": call_id, "type": "function",
                                    "function": {"name": function.get("name") or "", "arguments": arguments}})
                    open_calls.append((call_id, str(function.get("name") or "")))
                entry_["tool_calls"] = written
                if not text:
                    entry_["content"] = None
            out.append(entry_)
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            tool_name = str(message.get("tool_name") or "")
            pick = next((i for i, (cid, name) in enumerate(open_calls)
                         if (call_id and cid == call_id) or (not call_id and tool_name and name == tool_name)), -1)
            if pick < 0 and not call_id and open_calls:
                pick = 0
            if pick >= 0:
                call_id = open_calls.pop(pick)[0]
            if not call_id:
                raise GatewayError(400, "invalid_request", f"messages[{index}]: a tool result with no tool call before it")
            out.append({"role": "tool", "tool_call_id": call_id, "content": text})
            if message.get("images"):
                out.append({"role": "user", "content": _content("", message["images"])})
        else:
            raise GatewayError(400, "invalid_request", f"messages[{index}]: unknown role {message.get('role')!r}")
    return out


def chat_body(body: dict[str, Any], *, generate: bool, stream: bool) -> dict[str, Any]:
    """An Ollama request as a Chat Completions request (port of ollamaChatBody)."""
    if generate:
        messages = [{"role": "system", "content": body["system"]}] if body.get("system") else []
        messages.append({"role": "user", "content": _content(str(body.get("prompt") or ""), body.get("images"))})
    else:
        messages = _messages(body.get("messages") or [])
    out: dict[str, Any] = {"model": body["model"], "messages": messages, "stream": stream}
    if not generate and body.get("tools"):
        out["tools"] = body["tools"]
    fmt = body.get("format")
    if fmt not in (None, ""):
        if fmt == "json":
            out["response_format"] = {"type": "json_object"}
        elif isinstance(fmt, dict):
            out["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
        else:
            raise GatewayError(400, "invalid_request", f"invalid format: {json.dumps(fmt)}")
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    for key in ("temperature", "top_p", "seed", "presence_penalty", "frequency_penalty", "stop"):
        if options.get(key) is not None:
            out[key] = options[key]
    predict = options.get("num_predict")
    if isinstance(predict, (int, float)) and predict > 0:
        out["max_tokens"] = int(predict)
    think = body.get("think")
    if isinstance(think, str):
        if think:
            out["reasoning_effort"] = think
    elif think is not None and not isinstance(think, bool):
        raise GatewayError(400, "invalid_request", f"invalid think value: {think}")
    return out


def parse_request(body: Any, *, generate: bool):
    """``(model name, parsed chat request, stream, loaded reply or None)``.

    A request with nothing to say is how a client warms a model up; it is
    answered as Ollama does, with the model loaded."""
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "invalid request: the body must be a JSON object")
    name = str(body.get("model") or "").strip()
    if not name:
        raise GatewayError(400, "invalid_request", "model is required")
    name = model_name(name)
    stream = body.get("stream") is not False
    if (generate and not body.get("prompt") and not body.get("images")) or (not generate and not body.get("messages")):
        if entry(name) is None:
            raise GatewayError(404, "not_found", f"model '{name}' not found")
        loaded: dict[str, Any] = {"model": name, "created_at": _now(), "done": True, "done_reason": "load"}
        if generate:
            loaded["response"] = ""
        else:
            loaded["message"] = {"role": "assistant", "content": ""}
        return name, None, stream, loaded
    from . import chat_completions
    chat = chat_body({**body, "model": name}, generate=generate, stream=stream)
    model, context, options, _, _ = chat_completions.parse_request(chat)
    fmt = chat.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        from openprogram.models.providers.structured_output import JsonSchemaOutput
        options.response_format = JsonSchemaOutput(schema=fmt["json_schema"]["schema"])
    elif fmt.get("type") == "json_object":
        context.system_prompt = ((context.system_prompt + "\n\n") if context.system_prompt else "") + \
            "Respond with a single JSON value and nothing else."
    return name, (model, context, options), stream, None


class _Reply:
    def __init__(self, name: str, generate: bool) -> None:
        self.name, self.generate = name, generate
        self.start = time.monotonic_ns()
        self.first: int | None = None

    def part(self, text: str, thinking: str, calls: list[dict[str, Any]] | None, message: AssistantMessage | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"model": self.name, "created_at": _now(), "done": message is not None}
        if self.generate:
            out["response"] = text
            if thinking:
                out["thinking"] = thinking
        else:
            body: dict[str, Any] = {"role": "assistant", "content": text}
            if thinking:
                body["thinking"] = thinking
            if calls:
                body["tool_calls"] = calls
            out["message"] = body
        if message is not None:
            end = time.monotonic_ns()
            first = self.first or end
            counts = cv.UsageCounts.of(message.usage)
            out.update({"done_reason": "length" if message.stop_reason == "length" else "stop",
                        "total_duration": end - self.start, "load_duration": 0,
                        "prompt_eval_count": counts.input + counts.cache_read + counts.cache_write,
                        "prompt_eval_duration": first - self.start, "eval_count": counts.output,
                        "eval_duration": end - first})
        return out


def _calls(message: AssistantMessage) -> list[dict[str, Any]]:
    return [{"id": block.id or f"call_{i}", "function": {"index": i, "name": block.name, "arguments": block.arguments or {}}}
            for i, block in enumerate(b for b in message.content if isinstance(b, ToolCall))]


def reply_body(name: str, generate: bool, message: AssistantMessage) -> dict[str, Any]:
    reply = _Reply(name, generate)
    text = "".join(b.text for b in message.content if isinstance(b, TextContent))
    thinking = "".join(b.thinking for b in message.content if isinstance(b, ThinkingContent) and not b.redacted)
    return reply.part(text, thinking, _calls(message), message)


async def stream_lines(call, name: str, generate: bool) -> AsyncIterator[bytes]:
    reply = _Reply(name, generate)

    def line(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    try:
        async for event in call.events():
            kind = getattr(event, "type", None)
            if kind in {"text_delta", "thinking_delta"} and event.delta:
                reply.first = reply.first or time.monotonic_ns()
                yield line(reply.part(event.delta if kind == "text_delta" else "",
                                      event.delta if kind == "thinking_delta" else "", None))
            elif kind == "done":
                calls = _calls(event.message)
                if calls and not generate:
                    yield line(reply.part("", "", calls))
                yield line(reply.part("", "", None, event.message))
    except GatewayError as error:
        yield line(error_body(error))
