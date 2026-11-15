"""A searched tool on Anthropic is loaded by ``tool_reference`` (deferred-tools A1).

The search's result is only ``tool_reference`` blocks, and the tool is
appended to the tools array with ``defer_loading``, carrying the definition
its load record kept. The array grows only at its end, so every byte sent
before stays as it was. Haiku, Bedrock Converse and every model without the
form keep the text result and the ordinary array entry.
"""
import asyncio
import json
from types import SimpleNamespace as NS

from openprogram.models.providers.adapters.anthropic import anthropic
from openprogram.models.providers.settings.tool_search_spec import tool_search_form
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


class _Stream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return NS(usage=NS(input_tokens=1, output_tokens=1), stop_reason="end_turn")


def _send(monkeypatch, context, model_id="claude-fable-5-2", provider="anthropic"):
    params, client = {}, {}

    def stream(**kwargs):
        params.update(kwargs)
        return _Stream()

    def build_client(*args, **kwargs):
        client.update(kwargs)
        return NS(messages=NS(stream=stream)), False

    monkeypatch.setattr("openprogram.models.auth.usage.acquire_pooled", lambda *args: None)
    monkeypatch.setattr(anthropic, "_build_client", build_client)
    model = Model(id=model_id, name="t", provider=provider, api="anthropic-messages",
                  base_url="https://api.anthropic.com")

    async def run():
        return [e async for e in anthropic.stream_simple(model, context, SimpleStreamOptions(api_key="k"))]

    asyncio.run(run())
    return params, client


_READ = Tool(name="read", description="Read a file.",
             parameters={"type": "object", "properties": {"path": {"type": "string"}}})
_SEARCH = Tool(name="tool_search", description="Load deferred tools.",
               parameters={"type": "object", "properties": {"select": {"type": "string"}}})
_CRON_RECORDED = {"name": "cron", "description": "Schedule a job.",
                  "parameters": {"type": "object", "properties": {"when": {"type": "string"}}}}


def _search_turn(details=None, *, is_error=False, text="Loaded 1 deferred tool. <functions>...</functions>"):
    return [
        UserMessage(content="schedule a job", timestamp=1),
        AssistantMessage(content=[ToolCall(id="toolu_1", name="tool_search",
                                           arguments={"select": "select:cron"})],
                         api="anthropic-messages", provider="anthropic", model="claude-fable-5-2",
                         stop_reason="toolUse", timestamp=2),
        ToolResultMessage(tool_call_id="toolu_1", tool_name="tool_search",
                          content=[TextContent(text=text)], is_error=is_error,
                          details=details if details is not None
                          else {"loads": [{"name": "cron", "definition": _CRON_RECORDED}]},
                          timestamp=3),
    ]


def _canon(value):
    return json.dumps(value, sort_keys=True)


def test_the_form_is_declared_for_anthropic_and_off_for_haiku_and_other_wires():
    assert tool_search_form("anthropic", "anthropic-messages",
                            NS(id="claude-fable-5-2", tool_search=None)) == "anthropic-reference"
    assert tool_search_form("anthropic", "anthropic-messages",
                            NS(id="claude-haiku-4-5", tool_search=None)) is None
    for provider in ("amazon-bedrock", "github-copilot", "vercel-ai-gateway", "opencode"):
        assert tool_search_form(provider, "anthropic-messages", NS(id="claude-fable-5-2")) is None


def test_loading_appends_one_deferred_entry_and_leaves_earlier_bytes_alone(monkeypatch):
    before = Context(messages=[UserMessage(content="schedule a job", timestamp=1)],
                     tools=[_READ, _SEARCH])
    after = Context(messages=_search_turn(), tools=[_READ, _SEARCH])
    first, client_first = _send(monkeypatch, before)
    second, client_second = _send(monkeypatch, after)

    assert [t["name"] for t in second["tools"]] == ["read", "tool_search", "cron"]
    assert second["tools"][-1]["defer_loading"] is True
    unmarked = [{k: v for k, v in t.items() if k != "cache_control"} for t in first["tools"]]
    assert [_canon({k: v for k, v in t.items() if k != "cache_control"}) for t in second["tools"][:2]] \
        == [_canon(t) for t in unmarked]
    # The breakpoint stays on the last tool that is not deferred.
    assert "cache_control" not in second["tools"][-1]
    assert client_first["tool_search"] is True and client_second["tool_search"] is True


def test_the_search_result_is_references_only_with_a_text_beside_it(monkeypatch):
    params, _ = _send(monkeypatch, Context(messages=_search_turn(), tools=[_READ, _SEARCH]))
    results = params["messages"][-1]["content"]
    assert results[0]["type"] == "tool_result"
    assert results[0]["content"] == [{"type": "tool_reference", "tool_name": "cron"}]
    assert results[1]["type"] == "text" and results[1]["text"] == anthropic.TOOL_LOADED_TEXT


def test_the_entry_keeps_the_recorded_definition_after_the_schema_changes(monkeypatch):
    changed = Tool(name="cron", description="Schedule a job, now with more options.",
                   parameters={"type": "object", "properties": {"when": {"type": "string"},
                                                                 "tz": {"type": "string"}}})
    params, _ = _send(monkeypatch, Context(messages=_search_turn(), tools=[_READ, _SEARCH, changed]))
    entries = [t for t in params["tools"] if t["name"] == "cron"]
    assert len(entries) == 1
    assert entries[0]["description"] == "Schedule a job."
    assert set(entries[0]["input_schema"]["properties"]) == {"when"}
    assert params["tools"][-1]["name"] == "cron"


def test_the_bytes_of_a_later_round_extend_the_earlier_one(monkeypatch):
    turn = _search_turn()
    first, _ = _send(monkeypatch, Context(messages=turn, tools=[_READ, _SEARCH]))
    later = turn + [
        AssistantMessage(content=[TextContent(text="Scheduled.")], api="anthropic-messages",
                         provider="anthropic", model="claude-fable-5-2", stop_reason="stop",
                         timestamp=4),
        UserMessage(content="thanks", timestamp=5),
    ]
    second, _ = _send(monkeypatch, Context(messages=later, tools=[_READ, _SEARCH]))

    def strip(blocks):
        return [{k: v for k, v in b.items() if k != "cache_control"} for b in blocks]

    assert _canon([strip([t])[0] for t in first["tools"]]) == _canon([strip([t])[0] for t in second["tools"]])
    for index, message in enumerate(first["messages"]):
        assert _canon(strip(message["content"])) == _canon(strip(second["messages"][index]["content"]))


def test_haiku_keeps_the_text_result_and_an_ordinary_entry(monkeypatch):
    cron = Tool(**_CRON_RECORDED)
    params, client = _send(monkeypatch, Context(messages=_search_turn(), tools=[_READ, _SEARCH, cron]),
                           model_id="claude-haiku-4-5")
    assert client["tool_search"] is False
    assert all("defer_loading" not in t for t in params["tools"])
    result = params["messages"][-1]["content"][0]
    assert result["content"][0]["type"] == "text"


def test_what_a_loading_search_also_said_goes_beside_the_references(monkeypatch):
    # ``select:cron,send_file``: cron is loaded and sent as a reference,
    # which drops the result text; that send_file is already callable is
    # the note, sent as the text beside the references.
    note = "`send_file` is already callable: it is in your tools list with its full schema."
    turn = _search_turn(details={"loads": [{"name": "cron", "definition": _CRON_RECORDED}],
                                 "note": note})
    params, _ = _send(monkeypatch, Context(messages=turn, tools=[_READ, _SEARCH]))
    result, beside = params["messages"][-1]["content"]
    assert result["content"] == [{"type": "tool_reference", "tool_name": "cron"}]
    assert beside["type"] == "text" and beside["text"] == note


def test_a_search_that_loaded_nothing_sends_its_text_once(monkeypatch):
    text = "`send_file` is already callable: it is in your tools list with its full schema."
    turn = _search_turn(details={"note": text}, text=text)
    params, _ = _send(monkeypatch, Context(messages=turn, tools=[_READ, _SEARCH]))
    (result,) = params["messages"][-1]["content"]
    assert [block["text"] for block in result["content"]] == [text]


def test_a_model_without_the_form_reads_the_search_back_as_text(monkeypatch):
    # A conversation that loaded the tool on Fable and moved to a provider
    # without the form: the result is the text the node kept.
    cron = Tool(**_CRON_RECORDED)
    params, _ = _send(monkeypatch, Context(messages=_search_turn(), tools=[_READ, _SEARCH, cron]),
                      provider="minimax")
    content = params["messages"][-1]["content"][0]["content"]
    assert content == [{"type": "text", "text": "Loaded 1 deferred tool. <functions>...</functions>"}]


def test_a_search_with_no_match_or_an_error_is_plain_text(monkeypatch):
    for turn in (_search_turn(details={}, text="No deferred tools matched query 'x'."),
                 _search_turn(is_error=True, text="Error: empty query.")):
        params, _ = _send(monkeypatch, Context(messages=turn, tools=[_READ, _SEARCH]))
        result = params["messages"][-1]["content"]
        assert result[0]["content"][0]["type"] == "text"
        assert len(result) == 1
        assert all("defer_loading" not in t for t in params["tools"])


def test_a_tool_a_summary_carries_is_an_ordinary_entry(monkeypatch):
    summary = UserMessage(content="Summary of earlier turns.", timestamp=1,
                          carried_tools=[{"name": "cron", "definition": _CRON_RECORDED}])
    cron = Tool(**_CRON_RECORDED)
    params, _ = _send(monkeypatch, Context(messages=[summary, *_search_turn()[1:]],
                                           tools=[_READ, _SEARCH, cron]))
    entries = [t for t in params["tools"] if t["name"] == "cron"]
    assert len(entries) == 1 and "defer_loading" not in entries[0]
