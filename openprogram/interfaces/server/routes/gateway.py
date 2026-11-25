"""Model gateway routes: harnesses call OpenProgram's models in their own protocol.

  POST /gateway/anthropic/v1/messages               Anthropic Messages (Claude Code)
  POST /gateway/anthropic/v1/messages/count_tokens  local estimate
  GET  /gateway/anthropic/v1/models                 the enabled models
  POST /gateway/openai/v1/responses                 OpenAI Responses (Codex)
  POST /gateway/openai/v1/chat/completions          OpenAI Chat Completions
  GET  /gateway/openai/v1/models                    the enabled models

The owner-auth middleware lets ``/gateway/`` requests in with the gateway key
or the owner token (``owner_auth.py``). Nothing the client sends beyond the
body (its key, its ``anthropic-beta`` flags) reaches the provider. The
existing ``/v1/chat/completions`` (an OpenProgram Agent as a model,
``openai_compatible.py``) is a different endpoint and is unchanged. See
``openprogram/models/gateway``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

_log = logging.getLogger(__name__)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _harness(request: Request, protocol: str) -> str:
    """The usage label of a request: the harness by its User-Agent, else the protocol."""
    agent = (request.headers.get("user-agent") or "").lower()
    if agent.startswith("claude-cli") or "claude-code" in agent:
        return "claude_code"
    if "codex" in agent:
        return "codex"
    return protocol


async def _body(request: Request) -> Any:
    from openprogram.models.gateway.conversion import GatewayError
    try:
        return json.loads(await request.body() or b"null")
    except ValueError as exc:
        raise GatewayError(400, "invalid_request", f"the request body is not JSON: {exc}") from exc


def _error(protocol: str, error) -> JSONResponse:
    if protocol == "anthropic":
        from openprogram.models.gateway.messages import error_body
    else:
        from openprogram.models.gateway.responses import error_body
    status = error.status if 400 <= error.status < 600 else 500
    return JSONResponse(error_body(error), status_code=status)


def _models_list() -> dict[str, Any]:
    from openprogram.models.gateway.conversion import enabled_model_refs
    return {"object": "list", "data": [
        {"id": row["id"], "object": "model", "created": 0, "owned_by": row["provider"],
         "type": "model", "display_name": row["name"]}
        for row in enabled_model_refs()]}


def register(app) -> None:
    from openprogram.models.gateway import chat_completions, messages, responses
    from openprogram.models.gateway.conversion import GatewayError
    from openprogram.models.gateway.stream import UpstreamCall

    @app.post("/gateway/anthropic/v1/messages")
    async def gateway_messages(request: Request):
        try:
            model, context, options, stream = messages.parse_request(await _body(request))
            call = await UpstreamCall.open(model, context, options, harness=_harness(request, "anthropic"))
            if stream:
                return StreamingResponse(messages.stream_events(call), media_type="text/event-stream",
                                         headers=_SSE_HEADERS)
            return JSONResponse(messages.message_body(model, await call.final()))
        except GatewayError as error:
            return _error("anthropic", error)

    @app.post("/gateway/anthropic/v1/messages/count_tokens")
    async def gateway_count_tokens(request: Request):
        try:
            return JSONResponse(messages.count_tokens(await _body(request)))
        except GatewayError as error:
            return _error("anthropic", error)

    @app.get("/gateway/anthropic/v1/models")
    def gateway_anthropic_models():
        listed = _models_list()["data"]
        return JSONResponse({"data": [{"type": "model", "id": m["id"], "display_name": m["display_name"],
                                       "created_at": "1970-01-01T00:00:00Z"} for m in listed],
                             "has_more": False, "first_id": listed[0]["id"] if listed else None,
                             "last_id": listed[-1]["id"] if listed else None})

    @app.post("/gateway/openai/v1/responses")
    async def gateway_responses(request: Request):
        try:
            parsed = responses.parse_request(await _body(request))
            call = await UpstreamCall.open(parsed.model, parsed.context, parsed.options,
                                           harness=_harness(request, "openai-responses"))
            if parsed.stream:
                return StreamingResponse(responses.stream_events(call, parsed), media_type="text/event-stream",
                                         headers=_SSE_HEADERS)
            return JSONResponse(responses.response_body(parsed, await call.final()))
        except GatewayError as error:
            return _error("openai", error)

    @app.post("/gateway/openai/v1/chat/completions")
    async def gateway_chat_completions(request: Request):
        try:
            model, context, options, stream, include_usage = chat_completions.parse_request(await _body(request))
            call = await UpstreamCall.open(model, context, options, harness=_harness(request, "openai-chat"))
            if stream:
                return StreamingResponse(chat_completions.stream_events(call, include_usage),
                                         media_type="text/event-stream", headers=_SSE_HEADERS)
            return JSONResponse(chat_completions.completion_body(model, await call.final()))
        except GatewayError as error:
            return _error("openai", error)

    @app.get("/gateway/openai/v1/models")
    def gateway_openai_models():
        return JSONResponse(_models_list())
