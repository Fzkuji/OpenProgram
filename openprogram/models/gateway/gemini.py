"""Google's Gemini API (``/v1beta``) in and out, for Gemini CLI and Google's SDKs.

Ported from yetone/magpie ``internal/gateway/gemini.go`` (MIT License,
Copyright (c) yetone):

  GET  /v1beta/models                               the enabled models
  POST /v1beta/models/{model}:generateContent       one reply
  POST /v1beta/models/{model}:streamGenerateContent a stream (``alt=sse``)
  POST /v1beta/models/{model}:countTokens           a local estimate

The model is in the path; the route folds it and whether the call streams
into the request. Fields come in snake_case or camelCase (Google's proto
JSON reads both); everything but the caller's own values (a call's args, a
result's response, the schemas) is read as camelCase. A function's result
is the user's side of the turn whatever role it came under, and is matched
to its call by id, else to the first unanswered call of that name. A
``thoughtSignature`` goes out wrapped with the model that made it
(``conversion.wrap_signature``) and reaches that model only.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    ImageContent,
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

_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
           429: "RESOURCE_EXHAUSTED", 499: "CANCELLED", 500: "INTERNAL", 502: "UNAVAILABLE",
           503: "UNAVAILABLE", 504: "DEADLINE_EXCEEDED"}
#: Values that are the caller's own: their keys are kept as they came.
_OWN = frozenset({"args", "response", "parameters", "parametersJsonSchema", "parameters_json_schema",
                  "responseSchema", "response_schema", "responseJsonSchema", "response_json_schema"})
_GENERATE = ("generateContent", "streamGenerateContent", "countTokens")


def error_body(error: GatewayError) -> dict[str, Any]:
    status = error.status if 400 <= error.status < 600 else 500
    return {"error": {"code": status, "message": error.message, "status": _STATUS.get(status, "UNKNOWN")}}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def camel_keys(value: Any) -> Any:
    """``value`` with Google's own keys in camelCase (port of geminiCamel)."""
    if isinstance(value, list):
        return [camel_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        name = _camel(key) if "_" in key else key
        out[name] = item if key in _OWN or name in _OWN else camel_keys(item)
    return out


def json_schema(schema: Any) -> Any:
    """Gemini's OpenAPI-flavoured schema as JSON Schema: type names lower
    case, ``propertyOrdering`` and ``example`` left out, ``nullable`` as
    ``x-nullable`` (port of jsonSchema)."""
    if isinstance(schema, list):
        return [json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type":
            if isinstance(value, str):
                out[key] = value.lower()
            elif isinstance(value, list):
                out[key] = [v.lower() if isinstance(v, str) else v for v in value]
            else:
                out[key] = value
        elif key == "nullable":
            if value is True:
                out["x-nullable"] = True
        elif key in {"propertyOrdering", "example"}:
            continue
        else:
            out[key] = json_schema(value)
    return out


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = value.get("parts")
    return "".join(str(p.get("text") or "") for p in value or [] if isinstance(p, dict))


def _effort(config: dict[str, Any]) -> str | None:
    """thinkingConfig's level, else its budget as the nearest level, -1 as medium."""
    level = config.get("thinkingLevel")
    if isinstance(level, str) and level:
        return level.lower()
    budget = config.get("thinkingBudget")
    if isinstance(budget, int) and not isinstance(budget, bool):
        if budget < 0:
            return "medium"
        return "off" if budget == 0 else cv.budget_level(budget)
    return None


def level_asked(body: Any) -> bool:
    """The request names a thinking level of its own (magpie ``agyLevelAsked``):
    Antigravity CLI's ``/effort`` on a Google model it knows, or ``--effort``
    at launch, the user's pick of the moment, which beats the agent's pick
    (``agent_effort``) that stands for sessions sending only a budget of -1."""
    if not isinstance(body, dict):
        return False
    for config_key, thinking_key, level_key in (("generationConfig", "thinkingConfig", "thinkingLevel"),
                                                 ("generation_config", "thinking_config", "thinking_level")):
        config = body.get(config_key)
        thinking = config.get(thinking_key) if isinstance(config, dict) else None
        level = thinking.get(level_key) if isinstance(thinking, dict) else None
        if isinstance(level, str) and level.strip():
            return True
    return False


def _result_text(response: Any) -> tuple[str, bool]:
    """What a tool said: Gemini CLI wraps it in ``{output}`` or ``{error}``."""
    if isinstance(response, dict) and len(response) == 1:
        if "error" in response:
            value = response["error"]
            return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), True
        if "output" in response:
            value = response["output"]
            return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), False
    if isinstance(response, str):
        return response, False
    if response is None:
        return "", False
    return json.dumps(response, ensure_ascii=False), False


def split_call(call: str) -> tuple[str, str]:
    """``models/x:generateContent`` as ``(x, generateContent)``, split at the
    last colon (a model id may hold colons of its own)."""
    name, _, method = call.rpartition(":")
    if name.startswith("models/"):
        name = name[len("models/"):]
    if not name or method not in _GENERATE:
        raise GatewayError(404, "not_found", f"unknown method {call!r}")
    return name, method


def parse_request(body: Any, model_name: str, stream: bool) -> tuple[Model, Context, SimpleStreamOptions, bool]:
    """``(model, context, options, stream)`` of a generateContent request."""
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "the request body must be a JSON object")
    body = camel_keys(body)
    model, asked = cv.resolve_model_at(model_name)
    system = _text(body.get("systemInstruction"))
    config = body.get("generationConfig") if isinstance(body.get("generationConfig"), dict) else {}
    response_format = None
    if str(config.get("responseMimeType") or "").startswith("application/json"):
        ask = "Respond with a single JSON value and nothing else"
        schema = config.get("responseJsonSchema") or config.get("responseSchema")
        if isinstance(schema, dict):
            schema = json_schema(schema)
            ask += ", matching this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)
            from openprogram.models.providers.structured_output import JsonSchemaOutput
            response_format = JsonSchemaOutput(schema=schema)
        system = (system + "\n\n" if system else "") + ask + "."

    messages: list[Any] = []
    open_calls: dict[str, list[str]] = {}
    latest: dict[str, str] = {}
    for content in body.get("contents") or []:
        if not isinstance(content, dict):
            continue
        role = "assistant" if content.get("role") in {"model", "assistant"} else "user"
        side, parts = role, []

        def turn(want: str) -> None:
            nonlocal side, parts
            if side != want:
                _flush(model, messages, side, parts)
                side, parts = want, []

        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("functionResponse"), dict):
                turn("user")
                response = part["functionResponse"]
                name = str(response.get("name") or "")
                call_id = str(response.get("id") or "")
                if not call_id:
                    queue = open_calls.get(name) or []
                    call_id = queue[0] if queue else latest.get(name, "")
                if call_id in open_calls.get(name, []):
                    open_calls[name].remove(call_id)
                text, is_error = _result_text(response.get("response"))
                parts.append(ToolResultMessage(tool_call_id=call_id, tool_name=name,
                                               content=[TextContent(text=text)], is_error=is_error,
                                               timestamp=cv.now_ms()))
                continue
            turn(role)
            if isinstance(part.get("functionCall"), dict):
                call = part["functionCall"]
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or cv.new_id("call_"))
                latest[name] = call_id
                open_calls.setdefault(name, []).append(call_id)
                origin, raw = cv.unwrap_signature(part.get("thoughtSignature"))
                kept = raw if origin == cv.model_ref(model) else None
                parts.append(ToolCall(id=call_id, name=name, arguments=cv.json_arguments(call.get("args")),
                                      thought_signature=kept))
            elif isinstance(part.get("inlineData"), dict):
                data = part["inlineData"]
                mime = str(data.get("mimeType") or "")
                if mime.startswith("image/") and data.get("data"):
                    parts.append(ImageContent(data=str(data["data"]), mime_type=mime))
                else:
                    parts.append(TextContent(text=f"[{mime or 'file'} not passed to this model]"))
            elif part.get("thought"):
                thinking = cv.thinking_for(model, str(part.get("text") or ""), part.get("thoughtSignature"),
                                           raw_is_anthropic=False)
                if thinking is not None and role == "assistant":
                    parts.append(thinking)
            elif part.get("text"):
                parts.append(TextContent(text=str(part["text"])))
        _flush(model, messages, side, parts)

    tools, choice = _tools(body)
    effort = asked or (_effort(config.get("thinkingConfig")) if isinstance(config.get("thinkingConfig"), dict) else None)
    options = SimpleStreamOptions(
        max_tokens=config.get("maxOutputTokens") if isinstance(config.get("maxOutputTokens"), int) else None,
        temperature=config.get("temperature") if isinstance(config.get("temperature"), (int, float)) else None,
        tool_choice=choice,
        reasoning=cv.reasoning_level(model, effort) if effort else None,
        response_format=response_format,
    )
    context = Context(system_prompt=system or None, messages=messages, tools=tools)
    return model, context, options, stream


def _flush(model: Model, messages: list[Any], side: str, parts: list[Any]) -> None:
    if not parts:
        return
    if side == "assistant":
        messages.append(cv.assistant_message(model, list(parts)))
        return
    user: list[Any] = []
    for part in parts:
        if isinstance(part, ToolResultMessage):
            if user:
                messages.append(UserMessage(content=user, timestamp=cv.now_ms()))
                user = []
            messages.append(part)
        else:
            user.append(part)
    if user:
        messages.append(UserMessage(content=user, timestamp=cv.now_ms()))


def _tools(body: dict[str, Any]) -> tuple[list[Tool] | None, Any]:
    config = ((body.get("toolConfig") or {}).get("functionCallingConfig") or {}) if isinstance(body.get("toolConfig"), dict) else {}
    mode = str(config.get("mode") or "").upper()
    allowed = [str(n) for n in config.get("allowedFunctionNames") or []]
    only = set(allowed) if mode in {"ANY", "VALIDATED"} and allowed else None
    tools: list[Tool] = []
    for entry in body.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        for declaration in entry.get("functionDeclarations") or []:
            if not isinstance(declaration, dict) or not declaration.get("name"):
                continue
            if only is not None and declaration["name"] not in only:
                continue
            schema = declaration.get("parametersJsonSchema")
            if not isinstance(schema, dict):
                schema = json_schema(declaration.get("parameters")) if isinstance(declaration.get("parameters"), dict) else None
            tools.append(Tool(name=declaration["name"], description=str(declaration.get("description") or ""),
                              parameters=schema or {"type": "object", "properties": {}}))
    choice: Any = None
    if config:
        if mode == "NONE":
            choice = "none"
        elif mode == "ANY":
            if len(allowed) == 1 and len(tools) == 1 and tools[0].name == allowed[0]:
                choice = {"type": "function", "name": allowed[0]}
            else:
                choice = "required"
        else:
            choice = "auto"
    return tools or None, choice


# Reply

def _finish(stop_reason: str | None) -> str:
    return "MAX_TOKENS" if stop_reason == "length" else "STOP"


def _usage(message: AssistantMessage | None) -> dict[str, Any]:
    counts = cv.UsageCounts.of(getattr(message, "usage", None))
    prompt = counts.input + counts.cache_read + counts.cache_write
    out: dict[str, Any] = {"promptTokenCount": prompt, "candidatesTokenCount": counts.output,
                           "totalTokenCount": prompt + counts.output}
    if counts.cache_read:
        out["cachedContentTokenCount"] = counts.cache_read
    return out


def _signature(model: Model, value: str | None) -> str | None:
    return cv.wrap_signature(model, value) if value else None


def _call_part(model: Model, call: ToolCall) -> dict[str, Any]:
    part: dict[str, Any] = {"functionCall": {"id": call.id, "name": call.name, "args": call.arguments or {}}}
    signature = _signature(model, call.thought_signature)
    if signature:
        part["thoughtSignature"] = signature
    return part


def _parts(model: Model, message: AssistantMessage) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent) and block.text:
            out.append({"text": block.text})
        elif isinstance(block, ThinkingContent) and not block.redacted:
            part: dict[str, Any] = {"text": block.thinking, "thought": True}
            signature = _signature(model, block.thinking_signature)
            if signature:
                part["thoughtSignature"] = signature
            if block.thinking or signature:
                out.append(part)
        elif isinstance(block, ToolCall):
            out.append(_call_part(model, block))
    return out


def _chunk(model: Model, response_id: str, parts: list[dict[str, Any]], finish: str | None = None,
           message: AssistantMessage | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "index": 0}
    if finish:
        candidate["finishReason"] = finish
    out: dict[str, Any] = {"candidates": [candidate], "modelVersion": cv.model_ref(model), "responseId": response_id}
    if message is not None:
        out["usageMetadata"] = _usage(message)
    return out


def response_body(model: Model, message: AssistantMessage) -> dict[str, Any]:
    return _chunk(model, cv.new_id("resp_"), _parts(model, message), _finish(message.stop_reason), message)


async def stream_events(call) -> AsyncIterator[bytes]:
    """The reply as ``data:`` lines, one candidate chunk each; a function call
    goes out whole when its arguments are complete."""
    model: Model = call.model
    response_id = cv.new_id("resp_")

    def line(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")

    try:
        async for event in call.events():
            kind = getattr(event, "type", None)
            if kind == "text_delta" and event.delta:
                yield line(_chunk(model, response_id, [{"text": event.delta}]))
            elif kind == "thinking_delta" and event.delta:
                yield line(_chunk(model, response_id, [{"text": event.delta, "thought": True}]))
            elif kind == "thinking_end":
                block = event.partial.content[event.content_index] if event.content_index < len(event.partial.content) else None
                signature = _signature(model, getattr(block, "thinking_signature", None))
                if signature:
                    yield line(_chunk(model, response_id, [{"text": "", "thought": True, "thoughtSignature": signature}]))
            elif kind == "toolcall_end":
                yield line(_chunk(model, response_id, [_call_part(model, event.tool_call)]))
            elif kind == "done":
                yield line(_chunk(model, response_id, [], _finish(event.message.stop_reason), event.message))
    except GatewayError as error:
        body = error_body(error)
        body["error"]["code"], body["error"]["status"] = 502, "UNAVAILABLE"
        yield line(body)


def count_tokens(body: Any) -> dict[str, int]:
    """A local estimate; a request wrapped in ``generateContentRequest`` is unwrapped."""
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "the request body must be a JSON object")
    body = camel_keys(body)
    if isinstance(body.get("generateContentRequest"), dict):
        body = body["generateContentRequest"]
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
            if isinstance(value.get("inlineData"), dict):
                images += 1
                return
            for key, item in value.items():
                if key not in {"role", "thoughtSignature", "id"}:
                    walk(item)

    walk(body.get("systemInstruction"))
    walk(body.get("contents"))
    if body.get("tools"):
        texts.append(json.dumps(body["tools"], ensure_ascii=False))
    return {"totalTokens": cv.estimate_tokens(texts, images)}


def models_body() -> dict[str, Any]:
    from .conversion import enabled_model_refs
    out = []
    for row in enabled_model_refs():
        entry: dict[str, Any] = {"name": f"models/{row['id']}", "displayName": row.get("name") or row["id"],
                                 "description": f"{row.get('name') or row['id']} via OpenProgram",
                                 "supportedGenerationMethods": list(_GENERATE)}
        if row.get("context_window"):
            entry["inputTokenLimit"] = int(row["context_window"])
        if row.get("max_tokens"):
            entry["outputTokenLimit"] = int(row["max_tokens"])
        if row.get("thinking_levels") or row.get("reasoning"):
            entry["thinking"] = True
        out.append(entry)
    return {"models": out}


