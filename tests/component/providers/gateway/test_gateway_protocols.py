"""The model gateway reads each client protocol into OpenProgram's context and
writes the provider's events back in it.

A scripted provider registered under its own API stands in for the model, so
the request goes through the real ``stream_simple`` path. Each test checks
what reached the provider (the parsed context and options) and the exact
event sequence the client receives.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.models.providers.catalog.api_registry import get_api_provider, register_api_provider
from openprogram.models.providers.types import (
    AssistantMessage,
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
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

_API = "gateway-test-api"
_PROVIDER = "gateway-test"
_MODEL_ID = "scripted-1"
REF = f"{_PROVIDER}/{_MODEL_ID}"
PNG = "iVBORw0KGgo="


class FakeProvider:
    """Yields one scripted reply per call: ("text", s) / ("thinking", s, sig) / ("tool", id, name, args) / ("error", reason)."""

    requires_credentials = False

    def __init__(self) -> None:
        self.replies: list[list[tuple]] = []
        self.calls: list[tuple] = []

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        self.calls.append((context.model_copy(deep=True), options))
        steps = self.replies.pop(0)
        content: list = []

        def msg(stop="stop", **extra):
            return AssistantMessage(content=list(content), api=model.api, provider=model.provider, model=model.id,
                                    stop_reason=stop, timestamp=0, **extra)

        yield EventStart(partial=msg())
        for step in steps:
            at = len(content)
            if step[0] == "text":
                content.append(TextContent(text=""))
                yield EventTextStart(content_index=at, partial=msg())
                content[at] = TextContent(text=step[1])
                yield EventTextDelta(content_index=at, delta=step[1], partial=msg())
                yield EventTextEnd(content_index=at, content=step[1], partial=msg())
            elif step[0] == "thinking":
                content.append(ThinkingContent(thinking=""))
                yield EventThinkingStart(content_index=at, partial=msg())
                content[at] = ThinkingContent(thinking=step[1])
                yield EventThinkingDelta(content_index=at, delta=step[1], partial=msg())
                content[at] = ThinkingContent(thinking=step[1], thinking_signature=step[2])
                yield EventThinkingEnd(content_index=at, content=step[1], partial=msg())
            elif step[0] == "tool":
                call = ToolCall(id=step[1], name=step[2], arguments=step[3])
                content.append(ToolCall(id=step[1], name=step[2]))
                yield EventToolCallStart(content_index=at, partial=msg())
                yield EventToolCallDelta(content_index=at, delta=json.dumps(step[3]), partial=msg())
                content[at] = call
                yield EventToolCallEnd(content_index=at, tool_call=call, partial=msg())
            elif step[0] == "error":
                yield EventError(reason="error", error=msg("error", error_message="upstream broke",
                                                           error_reason=step[1]))
                return
        tool = any(s[0] == "tool" for s in steps)
        final = msg("toolUse" if tool else "stop",
                    usage=Usage(input=100, output=20, cache_read=30, cache_write=5, total_tokens=155))
        yield EventDone(reason="toolUse" if tool else "stop", message=final)


@pytest.fixture
def provider(monkeypatch) -> Iterator[FakeProvider]:
    from openprogram.models.providers.catalog import api_registry
    from openprogram.models.gateway import conversion

    fake = FakeProvider()
    previous = get_api_provider(_API)
    register_api_provider(_API, fake)
    model = Model(id=_MODEL_ID, name="Scripted", api=_API, provider=_PROVIDER, base_url="http://gateway.invalid",
                  reasoning=True, thinking_levels=["low", "medium", "high"], context_window=64000)

    def resolve(ref):
        value = str(ref or "").removeprefix("openprogram:")
        if value != REF:
            raise conversion.GatewayError(404, "not_found", f"model {value!r} is not set up in OpenProgram")
        return model

    monkeypatch.setattr(conversion, "lookup_model", resolve)
    try:
        yield fake
    finally:
        if previous is None:
            api_registry._registry.pop(_API, None)
        else:
            register_api_provider(_API, previous)


@pytest.fixture
def client(provider) -> Iterator[TestClient]:
    from openprogram.interfaces.server.routes import gateway

    app = FastAPI()
    gateway.register(app)
    with TestClient(app) as test_client:
        yield test_client


def _sse(text: str) -> list[tuple[str | None, dict]]:
    events = []
    for chunk in text.strip().split("\n\n"):
        name, data = None, None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if data and data != "[DONE]":
            events.append((name, json.loads(data)))
    return events


# Anthropic Messages

def test_anthropic_request_reaches_the_provider_as_openprogram_context(client, provider) -> None:
    from openprogram.models.gateway.conversion import wrap_signature
    from openprogram.models.gateway import conversion

    model = conversion.resolve_model(REF)
    own = wrap_signature(model, "sig-own")
    other = wrap_signature(model.model_copy(update={"provider": "elsewhere"}), "sig-other")
    provider.replies.append([("text", "ok")])
    body = {
        "model": REF, "max_tokens": 1000, "stream": True,
        "system": [{"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": "Use tools."}],
        "thinking": {"type": "enabled", "budget_tokens": 8000},
        "tools": [{"name": "read", "description": "Read a file", "input_schema": {"type": "object"},
                   "cache_control": {"type": "ephemeral"}},
                  {"type": "web_search_20250305", "name": "web_search"}],
        "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
        "metadata": {"user_id": "session-1"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "look"},
                                         {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "mine", "signature": own},
                {"type": "thinking", "thinking": "theirs", "signature": other},
                {"type": "thinking", "thinking": "anthropic", "signature": "raw-anthropic-sig"},
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {"path": "a"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "file body"}], "is_error": False},
                {"type": "text", "text": "and now?", "cache_control": {"type": "ephemeral"}}]},
        ],
    }
    response = client.post("/gateway/anthropic/v1/messages", json=body)
    assert response.status_code == 200
    context, options = provider.calls[0]
    assert context.system_prompt == "Be brief.\n\nUse tools."
    # Anthropic's web search tool is the gateway's own web_search (test_gateway_web_search.py)
    assert [t.name for t in context.tools] == ["read", "web_search"]
    assert context.tools[0].cache_control == {"type": "ephemeral"}
    first, assistant, result, follow = context.messages
    assert isinstance(first, UserMessage) and isinstance(first.content[1], ImageContent)
    assert [type(b).__name__ for b in assistant.content] == ["ThinkingContent", "TextContent", "ToolCall"]
    assert assistant.content[0].thinking_signature == "sig-own"  # only this model's own signature
    assert assistant.content[2].arguments == {"path": "a"}
    assert isinstance(result, ToolResultMessage) and result.tool_name == "read"
    assert result.content[0].text == "file body"
    assert follow.content[0].cache_control == {"type": "ephemeral"}
    assert options.tool_choice == "required" and options.parallel_tool_calls is False
    assert options.reasoning == "medium" and options.max_tokens == 1000
    assert options.api_key is None and options.headers is None


def test_unsigned_thinking_never_goes_to_an_anthropic_model() -> None:
    from openprogram.models.gateway.conversion import thinking_for

    anthropic = Model(id="claude-x", name="x", api="anthropic-messages", provider="anthropic", base_url="")
    other = Model(id="m", name="m", api="openai-completions", provider="deepseek", base_url="")
    assert thinking_for(anthropic, "t", None, raw_is_anthropic=True) is None
    assert thinking_for(anthropic, "t", "real", raw_is_anthropic=True).thinking_signature == "real"
    assert thinking_for(other, "t", "real", raw_is_anthropic=True) is None
    assert thinking_for(other, "t", None, raw_is_anthropic=True).thinking == "t"


def test_anthropic_stream_writes_messages_events(client, provider) -> None:
    provider.replies.append([("thinking", "let me see", "sig-1"), ("text", "Reading it."),
                             ("tool", "call_1", "read", {"path": "a.txt"})])
    response = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    events = _sse(response.text)
    assert [name for name, _ in events] == [
        "message_start",
        "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop",
        "content_block_start", "content_block_delta", "content_block_stop",
        "content_block_start", "content_block_delta", "content_block_stop",
        "message_delta", "message_stop"]
    data = [payload for _, payload in events]
    assert data[0]["message"]["model"] == REF
    assert data[1]["content_block"]["type"] == "thinking"
    assert data[2]["delta"] == {"type": "thinking_delta", "thinking": "let me see"}
    signature = data[3]["delta"]["signature"]
    assert data[3]["delta"]["type"] == "signature_delta" and signature.startswith("opgw1.")
    assert data[6]["delta"] == {"type": "text_delta", "text": "Reading it."}
    assert data[8]["content_block"] == {"type": "tool_use", "id": "call_1", "name": "read", "input": {}}
    assert json.loads(data[9]["delta"]["partial_json"]) == {"path": "a.txt"}
    assert [d["index"] for d in data[1:11] if "index" in d] == [0, 0, 0, 0, 1, 1, 1, 2, 2, 2]
    assert data[11]["delta"]["stop_reason"] == "tool_use"
    assert data[11]["usage"] == {"input_tokens": 100, "output_tokens": 20,
                                 "cache_read_input_tokens": 30, "cache_creation_input_tokens": 5}

    # The signature comes back to the same model as its own value.
    provider.replies.append([("text", "done")])
    client.post("/gateway/anthropic/v1/messages", json={"model": REF, "max_tokens": 100, "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "let me see", "signature": signature},
                                          {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "a.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "x"}]}]})
    assistant = provider.calls[1][0].messages[1]
    assert assistant.content[0].thinking_signature == "sig-1"


def test_anthropic_whole_reply_and_errors(client, provider) -> None:
    provider.replies.append([("text", "hello")])
    whole = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}).json()
    assert whole["content"] == [{"type": "text", "text": "hello"}]
    assert whole["stop_reason"] == "end_turn" and whole["usage"]["input_tokens"] == 100

    provider.replies.append([("error", "rate_limit")])
    refused = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 10, "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert refused.status_code == 429
    assert refused.json()["error"]["type"] == "rate_limit_error"

    missing = client.post("/gateway/anthropic/v1/messages", json={
        "model": "nobody/none", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]})
    assert missing.status_code == 404 and missing.json()["type"] == "error"

    # After the first byte, an error is an in-stream event and nothing is sent again.
    provider.replies.append([("text", "partial"), ("error", "provider")])
    late = _sse(client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 10, "stream": True, "messages": [{"role": "user", "content": "hi"}]}).text)
    assert late[-1][0] == "error" and late[-1][1]["error"]["type"] == "api_error"


def test_count_tokens_is_a_local_estimate(client) -> None:
    counted = client.post("/gateway/anthropic/v1/messages/count_tokens", json={
        "model": "anything", "system": "abcd" * 10, "messages": [{"role": "user", "content": "你好"}]}).json()
    assert counted["input_tokens"] > 10


# OpenAI Responses

def test_responses_request_and_stream(client, provider) -> None:
    provider.replies.append([("thinking", "plan", "rs-sig"), ("text", "Patching."),
                             ("tool", "call_9", "shell", {"command": ["ls"]}),
                             ("tool", "call_10", "apply_patch", {"input": "*** Begin Patch"})])
    body = {
        "model": f"openprogram:{REF}", "stream": True, "instructions": "You are Codex.",
        "reasoning": {"effort": "high", "summary": "auto"}, "prompt_cache_key": "thread-1",
        "tools": [{"type": "function", "name": "shell", "description": "Run", "parameters": {"type": "object"}},
                  {"type": "custom", "name": "apply_patch", "description": "Patch files",
                   "format": {"type": "grammar", "syntax": "lark", "definition": "start: patch"}},
                  {"type": "web_search"}],
        "input": [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Sandbox: none."}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]},
            {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{\"command\": [\"pwd\"]}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "/repo"},
        ],
    }
    events = _sse(client.post("/gateway/openai/v1/responses", json=body).text)
    context, options = provider.calls[0]
    assert context.system_prompt == "You are Codex.\n\nSandbox: none."
    assert [t.name for t in context.tools] == ["shell", "apply_patch"]
    assert "start: patch" in context.tools[1].description
    user, assistant, result = context.messages
    assert assistant.content[0].arguments == {"command": ["pwd"]}
    assert result.tool_name == "shell" and result.content[0].text == "/repo"
    assert options.reasoning == "high"

    names = [name for name, _ in events]
    assert names[:2] == ["response.created", "response.in_progress"]
    assert names[-1] == "response.completed"
    assert "response.reasoning_summary_text.delta" in names
    assert "response.output_text.delta" in names
    assert "response.function_call_arguments.delta" in names
    done_items = [payload["item"] for name, payload in events if name == "response.output_item.done"]
    assert [item["type"] for item in done_items] == ["reasoning", "message", "function_call", "custom_tool_call"]
    assert done_items[0]["encrypted_content"].startswith("opgw1.")
    assert done_items[1]["content"][0]["text"] == "Patching."
    assert json.loads(done_items[2]["arguments"]) == {"command": ["ls"]}
    assert done_items[3]["input"] == "*** Begin Patch" and done_items[3]["call_id"] == "call_10"
    completed = events[-1][1]["response"]
    assert completed["usage"] == {"input_tokens": 135, "input_tokens_details": {"cached_tokens": 30},
                                  "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 0},
                                  "total_tokens": 155}
    assert [e[1]["sequence_number"] for e in events] == list(range(len(events)))

    # Codex sends reasoning and the custom call back; both reach the same model.
    provider.replies.append([("text", "ok")])
    client.post("/gateway/openai/v1/responses", json={"model": REF, "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
        done_items[0], done_items[3],
        {"type": "custom_tool_call_output", "call_id": "call_10", "output": "Done!"}],
        "tools": body["tools"]})
    _, assistant, result = provider.calls[1][0].messages
    assert assistant.content[0].thinking_signature == "rs-sig"
    assert assistant.content[1].arguments == {"input": "*** Begin Patch"}
    assert result.tool_name == "apply_patch"


def test_responses_errors_use_openai_shape(client, provider) -> None:
    provider.replies.append([("error", "context")])
    refused = client.post("/gateway/openai/v1/responses", json={"model": REF, "input": "hi", "stream": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "context_length_exceeded"
    stateful = client.post("/gateway/openai/v1/responses", json={"model": REF, "input": "hi", "previous_response_id": "r"})
    assert stateful.status_code == 400

    provider.replies.append([("text", "partial"), ("error", "rate_limit")])
    late = _sse(client.post("/gateway/openai/v1/responses", json={"model": REF, "input": "hi", "stream": True}).text)
    assert late[-1][0] == "response.failed"
    assert late[-1][1]["response"]["error"]["code"] == "rate_limit_exceeded"


# OpenAI Chat Completions

def test_chat_completions_with_tools(client, provider) -> None:
    provider.replies.append([("text", "Checking."), ("tool", "call_2", "lookup", {"q": "x"})])
    events = _sse(client.post("/gateway/openai/v1/chat/completions", json={
        "model": REF, "stream": True, "stream_options": {"include_usage": True},
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"},
                     {"role": "assistant", "content": None, "tool_calls": [
                         {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]},
                     {"role": "tool", "tool_call_id": "call_1", "content": "found"}]}).text)
    context, options = provider.calls[0]
    assert context.system_prompt == "sys" and options.tool_choice == {"type": "function", "name": "lookup"}
    assert context.messages[2].tool_name == "lookup"
    deltas = [payload["choices"][0]["delta"] for _, payload in events if payload["choices"]]
    assert deltas[1] == {"content": "Checking."}
    assert deltas[2]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": "{\"q\":\"x\"}"}
    assert [p["choices"][0]["finish_reason"] for _, p in events if p["choices"]][-1] == "tool_calls"
    assert events[-1][1]["usage"]["prompt_tokens"] == 135

    provider.replies.append([("text", "plain")])
    whole = client.post("/gateway/openai/v1/chat/completions", json={
        "model": REF, "messages": [{"role": "user", "content": "q"}]}).json()
    assert whole["choices"][0]["message"]["content"] == "plain"


# A level asked inside the model id (magpie askedAt)

def test_asked_at_splits_a_level_only() -> None:
    from openprogram.models.gateway.conversion import asked_at
    assert asked_at("p/m:high") == ("p/m", "high")
    assert asked_at("p/m:OFF") == ("p/m", "none")
    assert asked_at("p/m:free") == ("p/m:free", "")
    assert asked_at("p/m:low+high") == ("p/m:low+high", "")
    assert asked_at("p/m") == ("p/m", "")


def test_a_level_in_the_model_id_is_asked_over_the_body(client, provider) -> None:
    provider.replies += [[("text", "a")], [("text", "b")], [("text", "c")], [("text", "d")]]
    client.post("/gateway/openai/v1/chat/completions", json={
        "model": REF + ":high", "reasoning_effort": "low", "messages": [{"role": "user", "content": "q"}]})
    client.post("/gateway/openai/v1/responses", json={"model": REF + ":low", "input": "hi"})
    client.post("/gateway/anthropic/v1/messages", json={
        "model": REF + ":medium", "max_tokens": 10, "messages": [{"role": "user", "content": "q"}]})
    client.post(f"/gateway/gemini/v1beta/models/{REF}:high:generateContent",
                json={"contents": [{"role": "user", "parts": [{"text": "q"}]}]})
    assert [options.reasoning for _, options in provider.calls] == ["high", "low", "medium", "high"]
    unknown = client.post("/gateway/openai/v1/chat/completions", json={
        "model": REF + ":turbo", "messages": [{"role": "user", "content": "q"}]})
    assert unknown.status_code == 404


def test_models_tell_cursor_private_inference_their_apis(monkeypatch) -> None:
    from openprogram.models.gateway import conversion, models_info
    rows = [{"id": "anthropic/claude-sonnet-4-5", "api": "anthropic-messages", "reasoning": True,
             "thinking_levels": ["low", "high"], "context_window": 200000, "max_tokens": 64000, "vision": True},
            {"id": "openai/gpt-5", "api": "openai-responses"},
            {"id": "deepseek/deepseek-chat", "api": "openai-completions"},
            {"id": "google/gemini-3", "api": "google-generative-ai"},
            {"id": "openrouter/anthropic/claude-x", "api": "openai-completions"}]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: rows)
    assert all("api_types" not in m for m in models_info.models_list()["data"])
    listed = {m["id"]: m for m in models_info.models_list(models_info.CURSOR_LOCAL)["data"]}
    assert listed["anthropic/claude-sonnet-4-5"]["api_types"] == ["anthropic_messages"]
    assert listed["anthropic/claude-sonnet-4-5"]["capabilities"] == {
        "supports_tool_use": True, "supports_streaming": True, "output_modalities": ["text"],
        "supports_reasoning": True, "reasoning_effort": ["low", "high"], "context_length": 200000,
        "max_output_tokens": 64000, "supports_vision": True}
    assert listed["openai/gpt-5"]["api_types"] == ["responses"]
    assert listed["deepseek/deepseek-chat"]["api_types"] == ["chat_completions"]
    assert listed["google/gemini-3"]["api_types"] == ["chat_completions", "responses", "anthropic_messages"]
    # a Claude its provider serves on Chat is asked there: no Anthropic Messages to prefer
    assert listed["openrouter/anthropic/claude-x"]["api_types"] == ["chat_completions"]


def test_cursor_private_inference_lists_with_its_own_key(monkeypatch) -> None:
    """Cursor Private Inference asks /models with the key it is given, so the
    list it reads carries api_types, which it picks a model's API from."""
    from openprogram.interfaces.server.routes import gateway
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import cursor_local
    from openprogram.models.gateway.keys import token_for
    monkeypatch.setattr(conversion, "enabled_model_refs",
                        lambda: [{"id": "anthropic/claude-sonnet-4-5", "api": "anthropic-messages"}])
    from openprogram.models.gateway import agents
    monkeypatch.setattr(agents, "all_agents", lambda: [cursor_local.agent()])
    app = FastAPI()
    gateway.register(app)
    with TestClient(app) as test_client:
        own = test_client.get("/gateway/openai/v1/models",
                              headers={"Authorization": f"Bearer {token_for(cursor_local.ID)}"}).json()
        plain = test_client.get("/gateway/openai/v1/models").json()
    assert own["data"][0]["api_types"] == ["anthropic_messages"]
    assert "api_types" not in plain["data"][0]
