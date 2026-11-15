"""Provider-side web search: capability, request tools, stream blocks, replay.

Contract: docs/reference/design/providers/web-search.html ("Wire contract").
"""
import asyncio
from types import SimpleNamespace as NS

import pytest

from openprogram.models.providers.adapters.anthropic import anthropic
from openprogram.models.providers.adapters.google import google as google_adapter
from openprogram.models.providers.adapters.google_gemini_cli import google_gemini_cli as gcli
from openprogram.models.providers.adapters.openai_codex import openai_codex as codex
from openprogram.models.providers.adapters.openai_responses import openai_responses as responses
from openprogram.models.providers.catalog.api_registry import resolve_api_provider_snapshot
from openprogram.models.providers.shared.openai_responses import (
    convert_responses_messages,
    process_responses_stream,
)
from openprogram.models.providers.shared.transform_messages import transform_messages
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    ServerToolUse,
    SimpleStreamOptions,
    TextContent,
    Tool,
    UserMessage,
)

_TOOL = Tool(
    name="read_file",
    description="Read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def _model(api, provider="p", model_id="m"):
    return Model(id=model_id, name=model_id, api=api, provider=provider, base_url="https://example.invalid")


# --------------------------------------------------------------------------
# Capability
# --------------------------------------------------------------------------

@pytest.mark.parametrize("api, expected", [
    ("openai-responses", True),
    ("openai-codex", True),
    ("anthropic-messages", True),
    # Not advertised: google_search cannot ride next to function declarations
    # on generateContent, and agent requests always carry function tools.
    ("google-generative-ai", False),
    ("gemini-subscription", False),
    ("openai-completions", False),
])
def test_snapshot_reports_web_search_capability(api, expected):
    assert resolve_api_provider_snapshot(_model(api)).supports_web_search is expected


def test_snapshot_web_search_is_keyword_only_default_false():
    from openprogram.models.providers.catalog.api_registry import ApiProviderSnapshot
    from openprogram.models.providers.structured_output import StructuredOutputCapabilities

    snap = ApiProviderSnapshot(object(), StructuredOutputCapabilities(), True)
    assert snap.supports_idempotency_key is True
    assert snap.supports_web_search is False


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tools", [None, [_TOOL]])
def test_responses_adds_web_search_tool(tools):
    model = _model("openai-responses", "openai", "gpt-5")
    params = responses._build_params(model, Context(messages=[], tools=tools), {"web_search": True})
    assert {"type": "web_search"} in params["tools"]
    assert len(params["tools"]) == (2 if tools else 1)


def test_responses_without_switch_has_no_web_search_tool():
    model = _model("openai-responses", "openai", "gpt-5")
    assert "tools" not in responses._build_params(model, Context(messages=[]), {})
    params = responses._build_params(model, Context(messages=[], tools=[_TOOL]), {"web_search": False})
    assert all(t.get("type") != "web_search" for t in params["tools"])


def test_codex_adds_web_search_without_function_tools():
    model = _model("openai-codex", "openai-codex", "gpt-5.5")
    body = codex._build_request_body(model, Context(messages=[]), SimpleStreamOptions(web_search=True), [])
    assert body["tools"] == [{"type": "web_search"}]
    assert body["tool_choice"] == "auto"
    off = codex._build_request_body(model, Context(messages=[]), SimpleStreamOptions(), [])
    assert "tools" not in off


class _AnthropicStream:
    def __init__(self, events=(), stop_reason="end_turn"):
        self._events = list(events)
        self._stop = stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def get_final_message(self):
        return NS(usage=NS(input_tokens=10, output_tokens=5), stop_reason=self._stop)


def _run_anthropic(monkeypatch, context, opts, events=()):
    params = {}

    def stream(**kwargs):
        params.update(kwargs)
        return _AnthropicStream(events)

    monkeypatch.setattr("openprogram.models.auth.usage.acquire_pooled", lambda *args: None)
    monkeypatch.setattr(anthropic, "_build_client", lambda *a, **k: (NS(messages=NS(stream=stream)), False))
    model = _model("anthropic-messages", "anthropic", "claude-sonnet-4-5")

    async def run():
        return [e async for e in anthropic.stream_simple(model, context, opts)]

    return params, asyncio.run(run())


@pytest.mark.parametrize("tools", [None, [_TOOL]])
def test_anthropic_adds_web_search_tool(monkeypatch, tools):
    params, _ = _run_anthropic(
        monkeypatch, Context(messages=[], tools=tools),
        SimpleStreamOptions(api_key="k", web_search=True),
    )
    assert params["tools"][-1] == {"type": "web_search_20250305", "name": "web_search"}
    assert len(params["tools"]) == (2 if tools else 1)


def test_anthropic_without_switch_has_no_web_search_tool(monkeypatch):
    params, _ = _run_anthropic(monkeypatch, Context(messages=[]), SimpleStreamOptions(api_key="k"))
    assert "tools" not in params


def _gcli_body(tools, opts):
    model = _model("gemini-subscription", "gemini-subscription", "gemini-2.5-pro")
    return gcli._build_request_body(model, Context(messages=[], tools=tools), opts, [], "proj")


def test_gemini_cli_google_search_without_function_tools():
    body = _gcli_body(None, {"web_search": True})
    assert body["request"]["tools"] == [{"google_search": {}}]
    assert "tools" not in _gcli_body(None, {})["request"]


def test_gemini_cli_function_tools_win_over_google_search():
    tools = _gcli_body([_TOOL], {"web_search": True})["request"]["tools"]
    assert len(tools) == 1 and "functionDeclarations" in tools[0]


def test_google_sdk_config_google_search():
    model = _model("google-generative-ai", "google", "gemini-2.5-flash")
    cfg = google_adapter._build_config(model, Context(messages=[]), SimpleStreamOptions(web_search=True))
    assert len(cfg.tools) == 1 and cfg.tools[0].google_search is not None
    cfg = google_adapter._build_config(model, Context(messages=[], tools=[_TOOL]), SimpleStreamOptions(web_search=True))
    assert len(cfg.tools) == 1 and cfg.tools[0].function_declarations
    assert google_adapter._build_config(model, Context(messages=[]), SimpleStreamOptions()).tools is None


# --------------------------------------------------------------------------
# Responses stream
# --------------------------------------------------------------------------

class _Collector:
    def __init__(self):
        self.events = []

    def push(self, evt):
        self.events.append(evt)


async def _feed(events):
    for e in events:
        yield e


def _responses_output(model):
    return AssistantMessage(content=[], api=model.api, provider=model.provider, model=model.id, timestamp=0)


def test_responses_stream_web_search_call_and_citations():
    model = _model("openai-codex", "openai-codex", "gpt-5.5")
    output = _responses_output(model)
    stream = _Collector()
    raw_item = {
        "type": "web_search_call", "id": "ws_1", "status": "completed",
        "action": {"type": "search", "query": "openprogram release",
                   "sources": [{"type": "url", "url": "https://a.example", "title": "A"},
                               {"type": "url", "url": "https://b.example"}]},
    }
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "web_search_call", "id": "ws_1", "status": "in_progress",
                  "action": {"type": "search", "query": "openprogram release"}}},
        {"type": "response.web_search_call.searching", "output_index": 0, "item_id": "ws_1"},
        {"type": "response.web_search_call.completed", "output_index": 0, "item_id": "ws_1"},
        {"type": "response.output_item.done", "output_index": 0, "item": raw_item},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "message", "id": "msg_1", "content": []}},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "See A."},
        {"type": "response.output_item.done", "output_index": 1,
         "item": {"type": "message", "id": "msg_1", "content": [{
             "type": "output_text", "text": "See A.",
             "annotations": [
                 {"type": "url_citation", "url": "https://a.example", "title": "A",
                  "start_index": 4, "end_index": 5},
                 {"type": "file_citation", "file_id": "f"},
             ]}]}},
        {"type": "response.completed", "response": {"status": "completed",
         "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
    ]
    asyncio.run(process_responses_stream(_feed(events), output, stream, model))

    types = [e["type"] for e in stream.events]
    assert types == ["servertool_start", "servertool_end", "text_start", "text_delta", "text_end"]
    start = stream.events[0]
    assert start["content_index"] == 0
    end = stream.events[1]["server_tool"]
    assert end["status"] == "completed"
    assert end["input"] == {"query": "openprogram release"}
    assert end["results"] == [{"url": "https://a.example", "title": "A"},
                              {"url": "https://b.example", "title": "https://b.example"}]
    assert end["raw"]["id"] == "ws_1" and "index" not in end

    stu, text = output.content
    assert isinstance(stu, ServerToolUse) and stu.id == "ws_1" and stu.name == "web_search"
    assert isinstance(text, TextContent)
    assert [(c.url, c.title, c.start, c.end) for c in text.citations] == [("https://a.example", "A", 4, 5)]
    # A provider-executed search never turns the turn into a tool round.
    assert output.stop_reason == "stop"


def test_responses_stream_failed_search():
    model = _model("openai-responses", "openai", "gpt-5")
    output = _responses_output(model)
    stream = _Collector()
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "web_search_call", "id": "ws_2"}},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "web_search_call", "id": "ws_2", "status": "failed",
                  "action": {"type": "search", "query": "q"}}},
    ]
    asyncio.run(process_responses_stream(_feed(events), output, stream, model))
    end = stream.events[-1]
    assert end["type"] == "servertool_end"
    assert end["server_tool"]["status"] == "failed" and end["server_tool"]["results"] == []


# --------------------------------------------------------------------------
# Anthropic stream
# --------------------------------------------------------------------------

class _Block(NS):
    def model_dump(self, mode=None):
        def plain(v):
            if isinstance(v, NS):
                return {k: plain(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [plain(x) for x in v]
            return v
        return plain(self)


def test_anthropic_stream_server_tool_use_and_citations(monkeypatch):
    events = [
        NS(type="content_block_start", index=0,
           content_block=_Block(type="server_tool_use", id="srvtoolu_1", name="web_search", input={})),
        NS(type="content_block_delta", index=0,
           delta=NS(type="input_json_delta", partial_json='{"query": "weather paris"}')),
        NS(type="content_block_stop", index=0),
        NS(type="content_block_start", index=1, content_block=_Block(
            type="web_search_tool_result", tool_use_id="srvtoolu_1",
            content=[NS(type="web_search_result", url="https://w.example", title="W",
                        encrypted_content="enc", page_age=None)])),
        NS(type="content_block_stop", index=1),
        NS(type="content_block_start", index=2, content_block=_Block(type="text", text="")),
        NS(type="content_block_delta", index=2, delta=NS(type="text_delta", text="Sunny.")),
        NS(type="content_block_delta", index=2, delta=NS(type="citations_delta", citation=_Block(
            type="web_search_result_location", url="https://w.example", title="W",
            cited_text="Sunny", encrypted_index="x"))),
        NS(type="content_block_stop", index=2),
    ]
    _, out = _run_anthropic(monkeypatch, Context(messages=[]),
                            SimpleStreamOptions(api_key="k", web_search=True), events)
    types = [e.type for e in out]
    assert types == ["start", "servertool_start", "servertool_end", "text_start", "text_delta", "text_end", "done"]
    end = out[2]
    assert end.content_index == 0
    assert end.server_tool.input == {"query": "weather paris"}
    assert end.server_tool.status == "completed"
    assert end.server_tool.results == [{"url": "https://w.example", "title": "W"}]
    final = out[-1].message
    assert final.stop_reason == "stop"
    stu, text = final.content
    assert isinstance(stu, ServerToolUse) and stu.raw["content"][0]["encrypted_content"] == "enc"
    assert text.text == "Sunny."
    assert [(c.url, c.title, c.start, c.end) for c in text.citations] == [("https://w.example", "W", None, None)]


def test_anthropic_search_error_marks_failed(monkeypatch):
    events = [
        NS(type="content_block_start", index=0,
           content_block=_Block(type="server_tool_use", id="srvtoolu_2", name="web_search",
                                input={"query": "q"})),
        NS(type="content_block_stop", index=0),
        NS(type="content_block_start", index=1, content_block=_Block(
            type="web_search_tool_result", tool_use_id="srvtoolu_2",
            content=NS(type="web_search_tool_result_error", error_code="max_uses_exceeded"))),
        NS(type="content_block_stop", index=1),
    ]
    _, out = _run_anthropic(monkeypatch, Context(messages=[]),
                            SimpleStreamOptions(api_key="k", web_search=True), events)
    end = [e for e in out if e.type == "servertool_end"][0]
    assert end.server_tool.status == "failed" and end.server_tool.input == {"query": "q"}


# --------------------------------------------------------------------------
# Gemini stream
# --------------------------------------------------------------------------

_GROUNDING = {
    "webSearchQueries": ["capital of france"],
    "groundingChunks": [{"web": {"uri": "https://g.example", "title": "G"}}],
    "groundingSupports": [{"segment": {"startIndex": 0, "endIndex": 5},
                           "groundingChunkIndices": [0]}],
}


def test_gemini_cli_grounding_blocks():
    output = {"content": [{"type": "text", "text": "Paris is the capital."}]}
    stream = _Collector()
    gcli._apply_grounding_citations(output, _GROUNDING)
    gcli._push_grounding_searches(output, stream, _GROUNDING)
    assert output["content"][0]["citations"] == [
        {"url": "https://g.example", "title": "G", "start": 0, "end": 5}]
    assert [e["type"] for e in stream.events] == ["servertool_start", "servertool_end"]
    block = stream.events[1]["server_tool"]
    assert block["input"] == {"query": "capital of france"}
    assert block["results"] == [{"url": "https://g.example", "title": "G"}]
    assert ServerToolUse.model_validate(block).status == "completed"


def test_google_sdk_stream_grounding(monkeypatch):
    grounding = NS(
        web_search_queries=["capital of france"],
        grounding_chunks=[NS(web=NS(uri="https://g.example", title="G"))],
        grounding_supports=[NS(segment=NS(start_index=0, end_index=5), grounding_chunk_indices=[0])],
    )
    chunk = NS(
        usage_metadata=None, prompt_feedback=None,
        candidates=[NS(
            finish_reason="STOP", grounding_metadata=grounding,
            content=NS(parts=[NS(function_call=None, text="Paris is the capital.",
                                 thought=False, thought_signature=None)]),
        )],
    )

    async def gen(**kwargs):
        async def it():
            yield chunk
        return it()

    class _Client:
        def __init__(self, **kwargs):
            self.aio = NS(models=NS(generate_content_stream=gen))

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    model = _model("google-generative-ai", "google", "gemini-2.5-flash")

    async def run():
        return [e async for e in google_adapter.stream_simple(
            model, Context(messages=[]), SimpleStreamOptions(api_key="k", web_search=True))]

    out = asyncio.run(run())
    types = [e.type for e in out]
    assert types == ["start", "text_start", "text_delta", "text_end",
                     "servertool_start", "servertool_end", "done"]
    final = out[-1].message
    assert final.stop_reason == "stop"
    text, stu = final.content
    assert text.citations[0].url == "https://g.example" and text.citations[0].end == 5
    assert isinstance(stu, ServerToolUse) and stu.input == {"query": "capital of france"}
    assert out[3].partial.content[0].citations  # citations ride with text_end


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

def _history(api, provider, model_id, block):
    return [
        UserMessage(content="search it", timestamp=0),
        AssistantMessage(
            content=[block, TextContent(text="Answer.", text_signature="msg_9",
                                        citations=[{"url": "https://a.example"}])],
            api=api, provider=provider, model=model_id, timestamp=0,
        ),
        UserMessage(content="thanks", timestamp=0),
    ]


def test_responses_replay_raw_item_and_no_tool_output():
    model = _model("openai-codex", "openai-codex", "gpt-5.5")
    raw = {"type": "web_search_call", "id": "ws_1", "status": "completed",
           "action": {"type": "search", "query": "q", "sources": None}}
    block = ServerToolUse(id="ws_1", name="web_search", input={"query": "q"}, raw=raw)
    items = convert_responses_messages(model, Context(messages=_history(model.api, model.provider, model.id, block)))
    calls = [i for i in items if i.get("type") == "web_search_call"]
    assert calls == [{"type": "web_search_call", "id": "ws_1", "status": "completed",
                      "action": {"type": "search", "query": "q"}}]
    assert not any(i.get("type") in ("function_call", "function_call_output") for i in items)
    message = [i for i in items if i.get("type") == "message"][0]
    assert message["content"][0]["annotations"] == []


def test_responses_replay_without_raw_builds_item():
    model = _model("openai-responses", "openai", "gpt-5")
    block = ServerToolUse(id="ws_3", name="web_search", input={"query": "q3"})
    items = convert_responses_messages(model, Context(messages=_history(model.api, model.provider, model.id, block)))
    assert {"type": "web_search_call", "id": "ws_3", "status": "completed",
            "action": {"type": "search", "query": "q3"}} in items


def test_responses_replay_drops_foreign_api_block():
    model = _model("openai-responses", "openai", "gpt-5")
    block = ServerToolUse(id="srvtoolu_1", name="web_search", input={"query": "q"})
    items = convert_responses_messages(
        model, Context(messages=_history("anthropic-messages", "anthropic", "claude", block)))
    assert not any(i.get("type") == "web_search_call" for i in items)


def test_anthropic_replay_server_tool_use_pair():
    model = _model("anthropic-messages", "anthropic", "claude-sonnet-4-5")
    result = {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
              "content": [{"type": "web_search_result", "url": "https://w.example",
                           "title": "W", "encrypted_content": "enc"}]}
    block = ServerToolUse(id="srvtoolu_1", name="web_search", input={"query": "q"}, raw=result)
    msgs = transform_messages(_history(model.api, model.provider, model.id, block), model)
    payload = anthropic._build_messages(Context(messages=msgs))
    assistant = [m for m in payload if m["role"] == "assistant"][0]["content"]
    assert assistant[0] == {"type": "server_tool_use", "id": "srvtoolu_1",
                            "name": "web_search", "input": {"query": "q"}}
    assert assistant[1] == {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                            "content": result["content"]}
    assert assistant[2] == {"type": "text", "text": "Answer."}
    # No tool_result is synthesized for a provider-executed search.
    assert not any(b.get("type") == "tool_result" for m in payload for b in m["content"])


def test_anthropic_replay_drops_foreign_or_rawless_block():
    model = _model("anthropic-messages", "anthropic", "claude-sonnet-4-5")
    foreign = ServerToolUse(id="ws_1", name="web_search", input={"query": "q"},
                            raw={"type": "web_search_call", "id": "ws_1"})
    msgs = transform_messages(_history("openai-codex", "openai-codex", "gpt-5.5", foreign), model)
    assert not any(isinstance(b, ServerToolUse) for m in msgs if m.role == "assistant" for b in m.content)
    rawless = ServerToolUse(id="srvtoolu_9", name="web_search", input={"query": "q"})
    msgs = transform_messages(_history(model.api, model.provider, model.id, rawless), model)
    payload = anthropic._build_messages(Context(messages=msgs))
    assistant = [m for m in payload if m["role"] == "assistant"][0]["content"]
    assert assistant == [{"type": "text", "text": "Answer."}]
