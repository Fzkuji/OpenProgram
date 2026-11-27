"""Model gateway routes: outside agents call OpenProgram's models in their own protocol.

  POST /gateway/anthropic/v1/messages               Anthropic Messages (Claude Code)
  POST /gateway/anthropic/v1/messages/count_tokens  local estimate
  GET  /gateway/anthropic/v1/models[/{id}]          the enabled models
  POST /gateway/openai/v1/responses                 OpenAI Responses (Codex)
  WS   /gateway/openai/v1/responses                 the same over a WebSocket
  POST /gateway/openai/v1/responses/compact         the provider's own compaction
  POST /gateway/openai/v1/chat/completions          OpenAI Chat Completions
  GET  /gateway/openai/v1/models[/{id}]             the enabled models, with metadata
                                                    (?format=opencode/opencode2: OpenCode's plugin)
  GET  /gateway/openai/v1                           what the gateway serves
  GET  /gateway/muse-code/models                    Muse Code's model list (asked at /muse-code/models)
  GET  /gateway/gemini/v1beta/models                Google's Gemini API (Gemini CLI)
  POST /gateway/gemini/v1beta/models/{model}:{method}
  GET  /gateway/ollama/api/version|tags             the Ollama API (VS Code's Copilot)
  POST /gateway/ollama/api/show|chat|generate
  POST /gateway/ollama/v1/chat/completions

``/gateway/agent/<id>/…`` serves the same routes for an agent named by its
address, and ``/gateway/agent/<id>.<mac>/…`` for one whose own key is in its
address (``attribution.py``). Claude Science is listed and served models as
``science.py`` says, Claude Desktop as ``desktop.py`` does. The owner-auth middleware lets ``/gateway/``
requests in with the gateway key, an agent's own key or the owner token
(``owner_auth.py``). Nothing the client sends beyond the body (its key, its
``anthropic-beta`` flags) reaches the provider. The existing
``/v1/chat/completions`` (an OpenProgram Agent as a model,
``openai_compatible.py``) is a different endpoint and is unchanged. See
``openprogram/models/gateway``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

_log = logging.getLogger(__name__)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _agent(request: Any, protocol: str) -> str:
    from openprogram.models.gateway.attribution import agent_of
    return agent_of(request, protocol)


async def _body(request: Request) -> Any:
    from openprogram.models.gateway.conversion import GatewayError
    try:
        return json.loads(await request.body() or b"null")
    except ValueError as exc:
        raise GatewayError(400, "invalid_request", f"the request body is not JSON: {exc}") from exc


def _error(protocol: str, error) -> JSONResponse:
    if protocol == "anthropic":
        from openprogram.models.gateway.messages import error_body
    elif protocol == "gemini":
        from openprogram.models.gateway.gemini import error_body
    elif protocol == "ollama":
        from openprogram.models.gateway.ollama import error_body
    else:
        from openprogram.models.gateway.responses import error_body
    status = error.status if 400 <= error.status < 600 else 500
    return JSONResponse(error_body(error), status_code=status)


def _web_search(query: str) -> tuple[str, list[dict[str, Any]]]:
    """A search for the gateway's Messages web search: OpenProgram's own web
    search tool, its saved default backend first."""
    from openprogram.programs.tools.web.web_search.web_search import search
    text, results = search(query)
    return text, [{"title": hit.title, "url": hit.url, "page_age": (hit.extras or {}).get("published") or None}
                  for hit in results]


def _anthropic_model(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "type": "model", "created_at": "2025-01-01T00:00:00Z"}


def register(app) -> None:
    from openprogram.models.gateway import (
        chat_completions,
        claude_subagent,
        compact,
        desktop,
        gemini,
        messages,
        models_info,
        ollama,
        responses,
        responses_ws,
        science,
        web_search,
    )
    from openprogram.models.gateway.conversion import GatewayError, level_in
    from openprogram.models.gateway.stream import UpstreamCall

    async def _open(request: Request, protocol: str, model, context, options, *,
                    level_asked: bool = False) -> UpstreamCall:
        # DeepSeek Harness's compactions go on the model picked for them
        from openprogram.models.gateway.dsh_compact import compact_to
        agent = _agent(request, protocol)
        return await UpstreamCall.open(compact_to(agent, request.headers, context, model), context, options,
                                       harness=agent, level_asked=level_asked)

    async def chat(request: Request, protocol: str):
        body = await _body(request)
        model, context, options, stream, include_usage = chat_completions.parse_request(body)
        call = await _open(request, protocol, model, context, options,
                           level_asked=level_in(body.get("model")))
        if stream:
            return StreamingResponse(chat_completions.stream_events(call, include_usage),
                                     media_type="text/event-stream", headers=_SSE_HEADERS)
        return JSONResponse(chat_completions.completion_body(model, await call.final()))

    # Anthropic

    web_search.provide(_web_search)

    @app.post("/gateway/anthropic/v1/messages")
    async def gateway_messages(request: Request):
        try:
            body, agent = await _body(request), _agent(request, "anthropic")
            if agent == science.SCIENCE_ID:
                body = science.request_body(body)
            elif agent == desktop.AGENT:
                body = desktop.shape(body, request.headers)
            elif agent == "claude_code":
                body = claude_subagent.move(body, request.headers)
            model, context, options, stream = messages.parse_request(body)
            level = level_in(body.get("model"))
            if web_search.ours(body, model):
                # the model's web_search calls answered by OpenProgram's own search
                call = await web_search.SearchCall(
                    model, context,
                    lambda ctx: _open(request, "anthropic", model, ctx, options, level_asked=level)).start()
            else:
                call = await _open(request, "anthropic", model, context, options, level_asked=level)
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
    def gateway_anthropic_models(request: Request):
        agent = _agent(request, "anthropic")
        if agent == science.SCIENCE_ID:
            return JSONResponse(science.models_list())
        if agent == desktop.AGENT:
            data = desktop.listing(request.headers.get("user-agent") or "")
            return JSONResponse({"data": [_anthropic_model(m) for m in data], "has_more": False,
                                 "first_id": data[0]["id"] if data else None,
                                 "last_id": data[-1]["id"] if data else None})
        listed = models_info.models_list()
        return JSONResponse({"data": [_anthropic_model(m) for m in listed["data"]], "has_more": False,
                             "first_id": listed.get("first_id"), "last_id": listed.get("last_id")})

    @app.get("/gateway/anthropic/v1/models/{model_id:path}")
    def gateway_anthropic_model(model_id: str, request: Request):
        agent = _agent(request, "anthropic")
        if agent == science.SCIENCE_ID:
            found = science.find_model(model_id)
        elif agent == desktop.AGENT:
            found = models_info.find_model(desktop.catalog_id(model_id).removesuffix("[1m]"))
        else:
            found = models_info.find_model(model_id)
        if found is None:
            return _error("anthropic", GatewayError(404, "not_found", f"model {model_id!r} is not set up in OpenProgram"))
        return JSONResponse(_anthropic_model(found))

    # OpenAI

    @app.get("/gateway/openai/v1")
    def gateway_openai_info():
        return JSONResponse(models_info.info())

    @app.post("/gateway/openai/v1/responses")
    async def gateway_responses(request: Request):
        try:
            body = await _body(request)
            parsed = responses.parse_request(body)
            call = await _open(request, "openai-responses", parsed.model, parsed.context, parsed.options,
                               level_asked=level_in(body.get("model")))
            if parsed.stream:
                return StreamingResponse(responses.stream_events(call, parsed), media_type="text/event-stream",
                                         headers=_SSE_HEADERS)
            return JSONResponse(responses.response_body(parsed, await call.final()))
        except GatewayError as error:
            return _error("openai", error)

    @app.websocket("/gateway/openai/v1/responses")
    async def gateway_responses_socket(websocket: WebSocket):
        await responses_ws.serve(websocket, _agent(websocket, "openai-responses"))

    @app.post("/gateway/openai/v1/responses/compact")
    async def gateway_responses_compact(request: Request):
        try:
            status, payload = await compact.compact(await _body(request), harness=_agent(request, "openai-responses"))
            return JSONResponse(payload, status_code=status)
        except GatewayError as error:
            return _error("openai", error)

    @app.post("/gateway/openai/v1/chat/completions")
    async def gateway_chat_completions(request: Request):
        try:
            return await chat(request, "openai-chat")
        except GatewayError as error:
            return _error("openai", error)

    @app.get("/gateway/openai/v1/models")
    def gateway_openai_models(request: Request, format: str = ""):
        if format in ("opencode", "opencode2"):
            # the model list OpenCode's plugin fetches as OpenCode starts
            # (magpie gateway/opencode_models.go): {"models": [{"id", "entry"}]}
            from openprogram.models.gateway.agents.opencode_format import served_models
            from openprogram.models.gateway.conversion import enabled_model_refs
            return JSONResponse({"models": served_models(enabled_model_refs(), format == "opencode2")},
                                headers={"Cache-Control": "no-store"})
        if format == models_info.ANTIGRAVITY_FORMAT:
            return JSONResponse(models_info.antigravity_list())
        return JSONResponse(models_info.models_list(_agent(request, "openai-chat")))

    @app.get("/gateway/openai/v1/models/{model_id:path}")
    def gateway_openai_model(model_id: str):
        found = models_info.find_model(model_id)
        if found is None:
            return _error("openai", GatewayError(404, "not_found", f"model {model_id!r} is not set up in OpenProgram"))
        return JSONResponse(found)

    @app.get("/gateway/muse-code/models")
    def gateway_muse_models():
        # Muse Code asks for it at its endpoint host's root, /muse-code/models,
        # served here as its own path (owner_auth, attribution.root_path)
        return JSONResponse(models_info.muse_models())

    # Gemini

    @app.get("/gateway/gemini/v1beta/models")
    def gateway_gemini_models():
        return JSONResponse(gemini.models_body())

    @app.post("/gateway/gemini/v1beta/models/{call:path}")
    async def gateway_gemini(call: str, request: Request):
        try:
            name, method = gemini.split_call(call)
            body = await _body(request)
            if method == "countTokens":
                return JSONResponse(gemini.count_tokens(body))
            stream = method == "streamGenerateContent"
            model, context, options, _ = gemini.parse_request(body, name, stream)
            agent = _agent(request, "gemini")
            upstream = await _open(request, "gemini", model, context, options,
                                   level_asked=(agent == "agy" and gemini.level_asked(body))
                                   or level_in(name))
            if stream:
                return StreamingResponse(gemini.stream_events(upstream), media_type="text/event-stream",
                                         headers=_SSE_HEADERS)
            return JSONResponse(gemini.response_body(model, await upstream.final()))
        except GatewayError as error:
            return _error("gemini", error)

    # Ollama

    @app.get("/gateway/ollama/api/version")
    def gateway_ollama_version():
        return JSONResponse(ollama.version())

    @app.get("/gateway/ollama/api/tags")
    def gateway_ollama_tags():
        return JSONResponse(ollama.tags())

    @app.post("/gateway/ollama/api/show")
    async def gateway_ollama_show(request: Request):
        try:
            return JSONResponse(ollama.show(await _body(request)))
        except GatewayError as error:
            return _error("ollama", error)

    async def ollama_chat(request: Request, generate: bool):
        try:
            name, parsed, stream, loaded = ollama.parse_request(await _body(request), generate=generate)
            if loaded is not None:
                if stream:
                    return JSONResponse(loaded, media_type="application/x-ndjson")
                return JSONResponse(loaded)
            model, context, options = parsed
            call = await UpstreamCall.open(model, context, options, harness=_agent(request, "ollama"))
            if stream:
                return StreamingResponse(ollama.stream_lines(call, name, generate),
                                         media_type="application/x-ndjson")
            return JSONResponse(ollama.reply_body(name, generate, await call.final()))
        except GatewayError as error:
            return _error("ollama", error)

    @app.post("/gateway/ollama/api/chat")
    async def gateway_ollama_chat(request: Request):
        return await ollama_chat(request, False)

    @app.post("/gateway/ollama/api/generate")
    async def gateway_ollama_generate(request: Request):
        return await ollama_chat(request, True)

    @app.post("/gateway/ollama/v1/chat/completions")
    async def gateway_ollama_chat_completions(request: Request):
        try:
            return await chat(request, "ollama")
        except GatewayError as error:
            return _error("openai", error)

    @app.get("/gateway/ollama/v1/models")
    def gateway_ollama_openai_models():
        return JSONResponse(models_info.models_list())
