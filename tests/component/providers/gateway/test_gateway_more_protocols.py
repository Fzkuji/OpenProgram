"""The gateway's other client protocols, after yetone/magpie's tests of the
same pieces: model metadata (``models_test.go``), ``/responses/compact``,
Responses over a WebSocket (``responses_ws_test.go``), Google's Gemini API
(``gemini_*_test.go``) and the Ollama API (``ollama_test.go``,
``ollama_chat_test.go``). The scripted provider of ``test_gateway_protocols``
stands in for the model."""
from __future__ import annotations

import json

import pytest

from openprogram.models.providers.types import ImageContent, ThinkingContent, ToolCall, ToolResultMessage

from .test_gateway_protocols import PNG, REF, _sse, client, provider  # noqa: F401 - fixtures


@pytest.fixture
def listed(monkeypatch):
    from openprogram.models.gateway import conversion
    rows = [{"id": REF, "provider": "gateway-test", "name": "Scripted", "context_window": 64000,
             "max_tokens": 8000, "vision": True, "reasoning": True,
             "thinking_levels": ["off", "low", "high"], "default_thinking_level": "low"},
            {"id": "plain/small", "provider": "plain", "name": "Small", "context_window": None,
             "max_tokens": None, "vision": False, "reasoning": False, "thinking_levels": []}]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: rows)
    return rows


# Model metadata

def test_models_carry_what_clients_read(client, listed) -> None:
    body = client.get("/gateway/openai/v1/models").json()
    first, second = body["data"]
    assert body["first_id"] == REF and body["last_id"] == "plain/small" and body["has_more"] is False
    assert first["context_window"] == first["context_length"] == first["max_input_tokens"] == 64000
    assert first["max_output_tokens"] == 8000
    assert first["reasoning"] is True and first["reasoning_levels"] == ["low", "high"]
    assert first["supported_reasoning_levels"] == [{"effort": "low"}, {"effort": "high"}]
    assert first["modalities"] == {"input": ["text", "image"], "output": ["text"]}
    assert first["tool_call"] is True and first["supported_parameters"] == ["tools", "tool_choice", "reasoning"]
    assert first["owned_by"] == "gateway-test" and first["display_name"] == "Scripted"
    assert "context_window" not in second and second["supported_parameters"] == ["tools", "tool_choice"]

    one = client.get(f"/gateway/openai/v1/models/openprogram:{REF}")
    assert one.status_code == 200 and one.json()["id"] == REF
    gone = client.get("/gateway/openai/v1/models/nope/none")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "model_not_found"
    assert client.get(f"/gateway/anthropic/v1/models/{REF}").json()["type"] == "model"
    assert client.get("/gateway/anthropic/v1/models/x/y").json()["type"] == "error"
    assert client.get("/gateway/openai/v1").status_code == 200


def test_compact_is_refused_for_a_model_off_the_responses_api(client) -> None:
    reply = client.post("/gateway/openai/v1/responses/compact", json={"model": REF, "input": "hi"})
    assert reply.status_code == 400
    assert "compact on /responses instead" in reply.json()["error"]["message"]


def test_compact_sends_only_an_api_key_and_records_usage(client, provider, monkeypatch) -> None:
    import httpx

    from openprogram.host.security import safe_http
    from openprogram.models.auth import resolver
    from openprogram.models.gateway import conversion
    from openprogram.models.providers.types import Model
    from openprogram.models.usage import recorder

    model = Model(id="gpt-x", name="X", api="openai-responses", provider="openai", base_url="http://up.invalid/v1")
    monkeypatch.setattr(conversion, "lookup_model", lambda ref: model)
    keys = {"openai": None}
    monkeypatch.setattr(resolver, "resolve_store_api_key_sync", lambda provider_id: keys[provider_id])
    monkeypatch.setattr(resolver, "resolve_api_key_sync",
                        lambda *a, **k: pytest.fail("a sign-in token may be sent upstream"))
    sent, recorded = [], []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            sent.append((url, json, headers))
            return httpx.Response(200, json={"output": [], "usage": {"input_tokens": 50, "output_tokens": 7,
                                                                       "input_tokens_details": {"cached_tokens": 10}}})
    monkeypatch.setattr(safe_http, "safe_async_client", lambda *a, **k: Client())
    monkeypatch.setattr(recorder, "record_message", lambda m, message, **k: recorded.append(message.usage))

    body = {"model": "openai/gpt-x", "input": "hi"}
    refused = client.post("/gateway/openai/v1/responses/compact", json=body)
    assert refused.status_code == 400 and "holds no API key" in refused.json()["error"]["message"] and not sent
    bad = client.post("/gateway/openai/v1/responses/compact", json={**body, "previous_response_id": "r"})
    assert bad.status_code == 400 and not sent

    keys["openai"] = "sk-test"
    reply = client.post("/gateway/openai/v1/responses/compact", json=body,
                        headers={"Authorization": "Bearer someone-elses-sign-in"})
    assert reply.status_code == 200
    [(url, json_sent, headers)] = sent
    assert url == "http://up.invalid/v1/responses/compact" and json_sent["model"] == "gpt-x"
    assert headers == {"Authorization": "Bearer sk-test"}
    [usage] = recorded
    assert (usage.input, usage.cache_read, usage.output) == (40, 10, 7)


# Responses over a WebSocket

def _turn(socket) -> list[dict]:
    frames = []
    while True:
        frame = json.loads(socket.receive_text())
        frames.append(frame)
        if frame["type"] in {"response.completed", "response.failed", "response.incomplete", "error"}:
            return frames


def test_socket_turns_continue_from_the_reply_before(client, provider) -> None:
    provider.replies += [[("text", "first")], [("text", "second")]]
    with client.websocket_connect("/gateway/openai/v1/responses") as socket:
        socket.send_text(json.dumps({"type": "response.create", "model": REF, "generate": False,
                                     "input": [{"type": "message", "role": "user",
                                                "content": [{"type": "input_text", "text": "warm"}]}]}))
        warm = _turn(socket)
        assert [f["type"] for f in warm] == ["response.created", "response.completed"]
        assert warm[-1]["response"]["usage"]["total_tokens"] == 0 and not provider.calls

        socket.send_text(json.dumps({"type": "response.create", "model": REF, "previous_response_id": warm[0]["response"]["id"],
                                     "input": [{"type": "message", "role": "user",
                                                "content": [{"type": "input_text", "text": "one"}]}]}))
        one = _turn(socket)
        assert one[0]["type"] == "response.created" and one[-1]["type"] == "response.completed"
        assert [m.content[0].text for m in provider.calls[0][0].messages] == ["warm", "one"]

        socket.send_text(json.dumps({"type": "response.create", "model": REF, "previous_response_id": one[0]["response"]["id"],
                                     "input": "two"}))
        _turn(socket)
        context = provider.calls[1][0]
        texts = [block.text for message in context.messages for block in message.content]
        assert texts == ["warm", "one", "first", "two"]

        socket.send_text(json.dumps({"type": "response.create", "model": REF, "previous_response_id": "resp_gone",
                                     "input": "three"}))
        error = _turn(socket)[-1]
        assert error["status"] == 400 and error["error"]["code"] == "previous_response_not_found"

        socket.send_text(json.dumps({"type": "response.interrupt", "response_id": "resp_x"}))
        assert json.loads(socket.receive_text())["type"] == "response.interrupt.failed"


def test_socket_closes_past_the_waiting_limit(monkeypatch) -> None:
    import asyncio

    from openprogram.models.gateway import responses_ws
    monkeypatch.setattr(responses_ws, "PENDING", 2)

    class Socket:
        """A client that sends faster than its replies go out."""

        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.closed = None

        async def accept(self) -> None:
            pass

        async def receive_text(self) -> str:
            await asyncio.sleep(0)
            return "not json"

        async def send_text(self, text: str) -> None:
            await asyncio.sleep(0.01)
            self.sent.append(json.loads(text))

        async def close(self, code: int = 1000) -> None:
            self.closed = code

    socket = Socket()
    asyncio.run(asyncio.wait_for(responses_ws.serve(socket, "codex"), 5))
    assert socket.sent[-1]["error"]["code"] == "too_many_messages" and socket.closed == 1008


def test_socket_keeps_two_replies() -> None:
    from openprogram.models.gateway.responses_ws import ResponsesSocket

    socket = ResponsesSocket(send=None, harness="codex")
    for n in range(3):
        socket.keep(f"r{n}", [n])
    assert list(socket.held) == ["r1", "r2"]


# Gemini

def test_gemini_request_reaches_the_provider(client, provider) -> None:
    provider.replies.append([("text", "done")])
    body = {
        "system_instruction": {"parts": [{"text": "Be brief."}]},
        "contents": [
            {"role": "user", "parts": [{"text": "read it"}, {"inline_data": {"mime_type": "image/png", "data": PNG}}]},
            {"role": "model", "parts": [{"functionCall": {"name": "read", "args": {"path": "a"}}},
                                        {"functionCall": {"name": "read", "args": {"path": "b"}}}]},
            # Antigravity CLI sends results as "model"; they are the user's side
            {"role": "model", "parts": [{"functionResponse": {"name": "read", "response": {"output": "A"}}},
                                        {"functionResponse": {"name": "read", "response": {"error": "no b"}}}]},
        ],
        "tools": [{"functionDeclarations": [{"name": "read", "description": "Read",
                                             "parameters": {"type": "OBJECT", "propertyOrdering": ["path"],
                                                            "properties": {"path": {"type": "STRING", "nullable": True}}}}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["read"]}},
        "generation_config": {"max_output_tokens": 500, "thinking_config": {"thinking_budget": 20000}},
    }
    reply = client.post(f"/gateway/gemini/v1beta/models/{REF}:generateContent", json=body).json()
    context, options = provider.calls[0]
    assert context.system_prompt == "Be brief."
    user, calls, first, second = context.messages
    assert isinstance(user.content[1], ImageContent)
    a, b = calls.content
    assert isinstance(a, ToolCall) and a.arguments == {"path": "a"}
    assert isinstance(first, ToolResultMessage) and first.tool_call_id == a.id and first.content[0].text == "A"
    assert second.tool_call_id == b.id and second.is_error
    assert context.tools[0].parameters == {"type": "object", "properties": {"path": {"type": "string", "x-nullable": True}}}
    assert options.tool_choice == {"type": "function", "name": "read"}
    assert options.max_tokens == 500 and options.reasoning == "high"
    assert reply["candidates"][0]["content"] == {"role": "model", "parts": [{"text": "done"}]}
    assert reply["candidates"][0]["finishReason"] == "STOP"
    assert reply["usageMetadata"] == {"promptTokenCount": 135, "candidatesTokenCount": 20,
                                      "totalTokenCount": 155, "cachedContentTokenCount": 30}


def test_gemini_stream_and_signatures(client, provider) -> None:
    provider.replies.append([("thinking", "hmm", "sig-1"), ("tool", "c1", "read", {"path": "x"})])
    text = client.post(f"/gateway/gemini/v1beta/models/{REF}:streamGenerateContent?alt=sse",
                       json={"contents": [{"role": "user", "parts": [{"text": "go"}]}]}).text
    assert ": " not in text.split("\n", 1)[0][:1]
    chunks = [payload for _, payload in _sse(text)]
    parts = [part for chunk in chunks for part in chunk["candidates"][0]["content"]["parts"]]
    assert parts[0] == {"text": "hmm", "thought": True}
    signed = [p for p in parts if p.get("thoughtSignature")]
    assert signed and signed[0]["thoughtSignature"].startswith("opgw1.")
    call = next(p for p in parts if "functionCall" in p)
    assert call["functionCall"] == {"id": "c1", "name": "read", "args": {"path": "x"}}
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP" and "usageMetadata" in chunks[-1]

    # the signed thought comes back to the model that made it
    provider.replies.append([("text", "ok")])
    client.post(f"/gateway/gemini/v1beta/models/{REF}:generateContent", json={"contents": [
        {"role": "user", "parts": [{"text": "go"}]},
        {"role": "model", "parts": [{"text": "hmm", "thought": True, "thoughtSignature": signed[0]["thoughtSignature"]}]},
        {"role": "user", "parts": [{"text": "more"}]}]})
    thinking = provider.calls[1][0].messages[1].content[0]
    assert isinstance(thinking, ThinkingContent) and thinking.thinking_signature == "sig-1"


def test_gemini_models_count_tokens_and_errors(client, listed) -> None:
    models = client.get("/gateway/gemini/v1beta/models").json()["models"]
    assert models[0]["name"] == f"models/{REF}"
    assert models[0]["supportedGenerationMethods"] == ["generateContent", "streamGenerateContent", "countTokens"]
    count = client.post(f"/gateway/gemini/v1beta/models/{REF}:countTokens",
                        json={"generateContentRequest": {"contents": [{"parts": [{"text": "hello world"}]}]}})
    assert count.json()["totalTokens"] > 0
    missing = client.post("/gateway/gemini/v1beta/models/no/model:generateContent", json={"contents": []})
    assert missing.status_code == 404 and missing.json()["error"]["status"] == "NOT_FOUND"
    assert client.post(f"/gateway/gemini/v1beta/models/{REF}:embed", json={}).status_code == 404


def test_gemini_schema_and_split() -> None:
    from openprogram.models.gateway.gemini import camel_keys, json_schema, split_call

    assert split_call("models/a/b:c:generateContent") == ("a/b:c", "generateContent")
    assert json_schema({"type": ["STRING", "NULL"], "example": 1}) == {"type": ["string", "null"]}
    assert camel_keys({"function_call": {"args": {"keep_me": 1}}}) == {"functionCall": {"args": {"keep_me": 1}}}


# Ollama

def test_ollama_model_routes(client, listed) -> None:
    assert client.get("/gateway/ollama/api/version").json() == {"version": "0.12.6"}
    tags = client.get("/gateway/ollama/api/tags").json()["models"]
    assert tags[0]["name"] == tags[0]["model"] == REF
    assert tags[0]["capabilities"] == ["completion", "tools", "vision", "thinking"]
    assert tags[1]["capabilities"] == ["completion", "tools"] and len(tags[0]["digest"]) == 64
    shown = client.post("/gateway/ollama/api/show", json={"name": f"{REF}:latest"}).json()
    assert shown["model_info"]["openprogram.context_length"] == 64000
    assert shown["model_info"]["general.basename"] == "Scripted"
    small = client.post("/gateway/ollama/api/show", json={"model": "plain/small"}).json()
    assert small["model_info"]["openprogram.context_length"] == 32768
    missing = client.post("/gateway/ollama/api/show", json={"model": "x"})
    assert missing.status_code == 404 and missing.json() == {"error": "model 'x' not found"}


def test_ollama_chat_streams_ndjson(client, provider, listed) -> None:
    provider.replies.append([("thinking", "hm", None), ("text", "hi"), ("tool", "c1", "look", {"q": 1})])
    reply = client.post("/gateway/ollama/api/chat", json={
        "model": f"{REF}:latest", "think": "high", "options": {"num_predict": 50, "num_ctx": 4096},
        "messages": [{"role": "user", "content": "see", "images": [PNG]},
                     {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "look", "arguments": {"q": 0}}}]},
                     {"role": "tool", "content": "zero", "tool_name": "look"}],
        "tools": [{"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}]})
    assert reply.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in reply.text.splitlines()]
    context, options = provider.calls[0]
    assert isinstance(context.messages[0].content[1], ImageContent)
    assert context.messages[1].content[0].arguments == {"q": 0}
    assert context.messages[2].tool_call_id == context.messages[1].content[0].id
    assert options.max_tokens == 50 and options.reasoning == "high"
    assert lines[0]["message"] == {"role": "assistant", "content": "", "thinking": "hm"}
    assert lines[1]["message"]["content"] == "hi"
    assert lines[-2]["message"]["tool_calls"] == [{"id": "c1", "function": {"index": 0, "name": "look", "arguments": {"q": 1}}}]
    last = lines[-1]
    assert last["done"] is True and last["done_reason"] == "stop"
    assert last["prompt_eval_count"] == 135 and last["eval_count"] == 20


def test_ollama_whole_reply_generate_and_warmup(client, provider, listed) -> None:
    provider.replies.append([("text", "whole")])
    one = client.post("/gateway/ollama/api/generate", json={"model": REF, "prompt": "hi", "system": "s", "stream": False}).json()
    assert one["response"] == "whole" and one["done"] is True
    assert provider.calls[0][0].system_prompt == "s"
    warm = client.post("/gateway/ollama/api/chat", json={"model": REF, "messages": []}).json()
    assert warm["done_reason"] == "load" and warm["message"]["content"] == ""
    assert client.post("/gateway/ollama/api/chat", json={"model": "nope", "messages": []}).status_code == 404
    odd = client.post("/gateway/ollama/api/chat", json={"model": REF, "stream": False, "messages": [
        {"role": "assistant", "content": "a", "tool_calls": ["junk", 3, {"function": "junk"}]},
        {"role": "user", "content": "b"}]})
    assert odd.status_code != 500
    bad = client.post("/gateway/ollama/api/chat", json={"model": REF, "messages": [{"role": "tool", "content": "x"}]})
    assert bad.status_code == 400 and "no tool call before it" in bad.json()["error"]
