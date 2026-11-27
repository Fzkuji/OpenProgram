"""Anthropic's web_search server tool on the gateway's /v1/messages, as
magpie's gateway carries it (``internal/gateway/search_test.go``): a provider
that searches by itself is asked to; any other model searches through
OpenProgram's own web search, and the client hears each search as
Anthropic's own server_tool_use and web_search_tool_result blocks."""
from __future__ import annotations

import json

import pytest

from openprogram.models.providers.catalog.api_registry import register_api_provider
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventServerToolEnd,
    EventStart,
    ServerToolUse,
    ToolResultMessage,
    Usage,
)

from .test_gateway_protocols import _API, REF, _sse, client, provider  # noqa: F401 - fixtures

SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
HITS = [{"title": "Release notes", "url": "https://example.com/notes", "page_age": "2 days ago"}]


@pytest.fixture
def searches(client):
    from openprogram.models.gateway import web_search
    asked: list[str] = []

    def search(query):
        asked.append(query)
        return f"1. Release notes - https://example.com/notes ({query})", HITS

    web_search.provide(search)
    yield asked
    web_search.provide(None)


def _search(query: str, at: str = "s") -> tuple:
    return ("tool", f"call_{at}", "web_search", {"query": query})


def test_the_gateway_searches_for_a_model_that_cannot(client, provider, searches) -> None:
    provider.replies += [[("text", "Looking."), _search("magpie release")], [("text", "It shipped.")]]
    response = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "stream": True, "tools": [SEARCH_TOOL],
        "messages": [{"role": "user", "content": "what's new?"}]})
    events = _sse(response.text)
    blocks = [payload["content_block"] for name, payload in events if name == "content_block_start"]
    assert [block["type"] for block in blocks] == ["text", "server_tool_use", "web_search_tool_result", "text"]
    use, result = blocks[1], blocks[2]
    assert use["id"].startswith("srvtoolu_") and use["name"] == "web_search" and result["tool_use_id"] == use["id"]
    query = next(p["delta"]["partial_json"] for name, p in events
                 if name == "content_block_delta" and p["index"] == 1)
    assert json.loads(query) == {"query": "magpie release"}
    assert result["content"] == [{"type": "web_search_result", "title": "Release notes",
                                  "url": "https://example.com/notes", "encrypted_content": "", "page_age": "2 days ago"}]
    end = next(p for name, p in events if name == "message_delta")
    assert end["delta"]["stop_reason"] == "end_turn"
    assert end["usage"]["input_tokens"] == 200  # both rounds
    assert searches == ["magpie release"]
    # the model got a web_search function tool and, next round, what was found
    first, second = provider.calls[0], provider.calls[1]
    assert [tool.name for tool in first[0].tools] == ["web_search"] and first[1].web_search is None
    answer = second[0].messages[-1]
    assert isinstance(answer, ToolResultMessage) and "example.com/notes" in answer.content[0].text


def test_a_whole_reply_and_the_clients_own_tools(client, provider, searches) -> None:
    provider.replies += [[_search("q1")], [("text", "Done.")]]
    whole = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "tools": [SEARCH_TOOL],
        "messages": [{"role": "user", "content": "hi"}]}).json()
    assert [block["type"] for block in whole["content"]] == ["server_tool_use", "web_search_tool_result", "text"]
    assert whole["content"][0]["input"] == {"query": "q1"} and whole["stop_reason"] == "end_turn"

    # a client tool already named web_search: the gateway's is another name,
    # and a round that calls the client's tool goes to the client
    own = {"name": "web_search", "description": "mine", "input_schema": {"type": "object"}}
    provider.replies.append([("tool", "call_c", "web_search", {"query": "x"})])
    reply = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "tools": [own, SEARCH_TOOL],
        "messages": [{"role": "user", "content": "hi"}]}).json()
    assert [tool.name for tool in provider.calls[-1][0].tools] == ["web_search", "openprogram_web_search"]
    assert reply["content"] == [{"type": "tool_use", "id": "call_c", "name": "web_search", "input": {"query": "x"}}]
    assert reply["stop_reason"] == "tool_use" and searches == ["q1"]

    # earlier searches in the history are the client's record, not the model's
    provider.replies.append([("text", "ok")])
    client.post("/gateway/anthropic/v1/messages", json={"model": REF, "max_tokens": 100, "messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": whole["content"]},
        {"role": "user", "content": "more"}]})
    assert [type(m).__name__ for m in provider.calls[-1][0].messages] == ["UserMessage", "AssistantMessage", "UserMessage"]


def test_searches_stop_after_six_rounds(client, provider, searches) -> None:
    provider.replies += [[_search(f"q{n}", str(n))] for n in range(1, 9)]
    reply = client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "tools": [SEARCH_TOOL],
        "messages": [{"role": "user", "content": "dig"}]}).json()
    assert searches == [f"q{n}" for n in range(1, 7)]
    told = provider.calls[7][0].messages[-1]
    assert told.is_error and "No more searches" in told.content[0].text
    assert reply["stop_reason"] == "end_turn" and len(provider.calls) == 8


def test_a_provider_that_searches_itself_is_asked_to(client, provider, searches) -> None:
    class Searching(type(provider)):
        supports_web_search = True

        async def stream_simple(self, model, context, options=None):
            self.calls.append((context.model_copy(deep=True), options))
            block = ServerToolUse(id="ws_1", name="web_search", input={"query": "own"}, results=HITS)

            def msg(content, stop="stop"):
                return AssistantMessage(content=content, api=model.api, provider=model.provider, model=model.id,
                                        stop_reason=stop, timestamp=0, usage=Usage(input=10, output=2))
            yield EventStart(partial=msg([]))
            yield EventServerToolEnd(content_index=0, server_tool=block, partial=msg([block]))
            yield EventDone(reason="stop", message=msg([block]))

    native = Searching()
    register_api_provider(_API, native)
    events = _sse(client.post("/gateway/anthropic/v1/messages", json={
        "model": REF, "max_tokens": 100, "stream": True, "tools": [SEARCH_TOOL],
        "messages": [{"role": "user", "content": "hi"}]}).text)
    blocks = [payload["content_block"] for name, payload in events if name == "content_block_start"]
    assert [block["type"] for block in blocks] == ["server_tool_use", "web_search_tool_result"]
    assert blocks[0]["id"] == "srvtoolu_ws_1" and blocks[1]["content"][0]["url"] == "https://example.com/notes"
    context, options = native.calls[0]
    assert options.web_search is True and not context.tools and searches == []
