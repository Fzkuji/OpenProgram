"""Provider-side web search: intent resolution, loop pass-through, projection.

Contract: docs/reference/design/providers/requests/web-search.html.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    EventDone,
    EventStart,
    EventTextEnd,
    TextContent,
)
from openprogram.runtime.agent import provider_search
from openprogram.runtime.agent.runtime import Runtime


def _server_tool(status="completed", results=None, query="ctxpress"):
    return SimpleNamespace(
        type="serverToolUse", id="ws_1", name="web_search",
        input={"query": query}, status=status,
        results=results if results is not None else [
            {"title": "ctxpress", "url": "https://github.com/example-org/ctxpress"},
            {"title": "Docs", "url": "https://docs.example.org"},
        ],
        raw=None,
    )


def _local_tool(executed):
    async def execute(call_id, arguments, cancel=None, on_update=None):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text="local results")])

    return AgentTool(name="web_search", label="Search", description="Local search",
                     parameters={"type": "object", "properties": {}}, execute=execute)


def _other_tool():
    async def execute(call_id, arguments, cancel=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="ok")])

    return AgentTool(name="read", label="Read", description="Read",
                     parameters={"type": "object", "properties": {}}, execute=execute)


def _snapshot(monkeypatch, **attrs):
    import openprogram.models.providers.catalog.api_registry as api_registry

    real = api_registry.resolve_api_provider_snapshot

    def fake(model):
        try:
            snap = real(model)
        except Exception:
            snap = None
        values = {name: getattr(snap, name) for name in dir(snap)
                  if not name.startswith("_") and not callable(getattr(snap, name))
                  } if snap is not None else {}
        values.pop("supports_web_search", None)
        values.update(attrs)
        return SimpleNamespace(**values)

    monkeypatch.setattr(api_registry, "resolve_api_provider_snapshot", fake)


# ── intent resolution ──────────────────────────────────────────────────


def test_supporting_provider_turns_the_option_on_and_drops_the_local_tool(monkeypatch):
    _snapshot(monkeypatch, supports_web_search=True)
    tools = [_other_tool(), _local_tool([])]

    option, kept = provider_search.resolve_web_search(object(), tools, True)

    assert option is True
    assert [t.name for t in kept] == ["read"]


def test_provider_without_search_keeps_the_local_tool(monkeypatch):
    _snapshot(monkeypatch, supports_web_search=False)
    tools = [_other_tool(), _local_tool([])]

    option, kept = provider_search.resolve_web_search(object(), tools, True)

    assert option is None
    assert kept is tools


def test_no_intent_changes_nothing(monkeypatch):
    _snapshot(monkeypatch, supports_web_search=True)
    tools = [_local_tool([])]

    assert provider_search.resolve_web_search(object(), tools, False) == (None, tools)


def test_models_layer_without_the_flag_keeps_an_explicit_option(monkeypatch):
    _snapshot(monkeypatch)  # no supports_web_search attribute at all
    tools = [_local_tool([])]

    assert provider_search.resolve_web_search(object(), tools, True, explicit=True) == (True, tools)
    assert provider_search.resolve_web_search(object(), tools, True) == (None, tools)


@pytest.mark.parametrize("override, profile, expected", [
    ({"inherit": True, "web_search": True}, None, True),
    ({"inherit": True, "web_search": False}, None, False),
    (["read", "web_search"], None, True),
    (["read"], None, False),
    (None, {"tools": {"mode": "selected", "web_search": True}}, True),
    (None, {"tools": {"mode": "automatic"}}, False),
    (None, None, False),
])
def test_session_search_intent(override, profile, expected):
    assert provider_search.session_search_intent(override, profile) is expected


def _capturing_stream(seen):
    async def stream(model, context, options):
        seen.append({
            "web_search": getattr(options, "web_search", None),
            "tools": [t.name for t in (context.tools or [])],
        })
        message = AssistantMessage(content=[TextContent(text="done")], api=model.api,
                                   provider=model.provider, model=model.id,
                                   stop_reason="stop", timestamp=1)
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)

    return stream


@pytest.mark.parametrize("supported, option, tools", [
    (True, True, ["read"]),
    (False, None, ["read", "web_search"]),
])
def test_runtime_exec_explicit_web_search_resolves_against_the_provider(
    monkeypatch, supported, option, tools,
):
    _snapshot(monkeypatch, supports_web_search=supported)
    seen = []
    runtime = Runtime(call=lambda *_a, **_k: "unused")
    try:
        assert runtime.exec("find it", tools=[_other_tool(), _local_tool([])],
                            stream_fn=_capturing_stream(seen), web_search=True) == "done"
    finally:
        runtime.close()
    assert seen == [{"web_search": option, "tools": tools}]


def test_runtime_exec_without_web_search_sends_no_option(monkeypatch):
    _snapshot(monkeypatch, supports_web_search=True)
    seen = []
    runtime = Runtime(call=lambda *_a, **_k: "unused")
    try:
        runtime.exec("find it", tools=[_other_tool(), _local_tool([])],
                     stream_fn=_capturing_stream(seen))
    finally:
        runtime.close()
    assert seen == [{"web_search": None, "tools": ["read", "web_search"]}]


# ── loop pass-through, projection, persistence (Runtime path) ──────────


@pytest.fixture
def lenient_content():
    """The real servertool events/blocks, once the models layer has them."""
    from openprogram.models.providers import types as t

    if not hasattr(t, "EventServerToolStart"):
        pytest.skip("models layer has no servertool events")


def _run_search_turn(monkeypatch):
    from openprogram.models.providers import types as t

    server_tool = t.ServerToolUse(
        id="ws_1", name="web_search", input={"query": "ctxpress"}, status="completed",
        results=[{"title": "ctxpress", "url": "https://github.com/example-org/ctxpress"},
                 {"title": "Docs", "url": "https://docs.example.org"}],
    )
    running = t.ServerToolUse(id="ws_1", name="web_search", input={"query": ""},
                              status="searching", results=[])
    text = TextContent(text="ctxpress compresses context.", citations=[
        {"url": "https://github.com/example-org/ctxpress", "title": "ctxpress",
         "start": 0, "end": 8}])
    calls, executed, events = [], [], []

    async def stream(model, context, options):
        calls.append(1)
        def msg(content):
            return AssistantMessage(content=content, api=model.api, provider=model.provider,
                                    model=model.id, stop_reason="stop", timestamp=1)
        yield EventStart(partial=msg([]))
        yield t.EventServerToolStart(content_index=0, partial=msg([running]))
        yield t.EventServerToolEnd(content_index=0, server_tool=server_tool,
                                   partial=msg([server_tool]))
        yield EventTextEnd(content_index=1, content=text.text, partial=msg([server_tool, text]))
        yield EventDone(reason="stop", message=msg([server_tool, text]))

    runtime = Runtime(call=lambda *_a, **_k: "unused")
    runtime.on_stream = events.append
    try:
        reply = runtime.exec("search", tools=[_local_tool(executed)], stream_fn=stream)
        blocks = list(runtime.last_blocks)
    finally:
        runtime.close()
    return reply, calls, executed, events, blocks


def test_server_tool_use_is_forwarded_and_never_executed(monkeypatch, lenient_content):
    reply, calls, executed, events, blocks = _run_search_turn(monkeypatch)

    assert reply == "ctxpress compresses context."
    assert calls == [1]
    assert executed == []
    tool_events = [e for e in events if e["type"] in {"tool_use", "tool_result"}]
    for event in tool_events:
        event.pop("elapsed")
    assert tool_events == [
        {"type": "tool_use", "tool_call_id": "ws_1", "occurrence_id": "ws_1",
         "tool": "web_search", "input": json.dumps({"query": ""}), "expose": "full",
         "provider_executed": True, "node_id": "", "ref_node_id": "", "group_id": ""},
        {"type": "tool_result", "tool_call_id": "ws_1", "occurrence_id": "ws_1",
         "tool": "web_search", "input": json.dumps({"query": "ctxpress"}),
         "result": "ctxpress — https://github.com/example-org/ctxpress\n"
                   "Docs — https://docs.example.org",
         "expose": "full", "provider_executed": True, "is_error": False,
         "outcome": "completed", "node_id": "", "ref_node_id": "", "group_id": ""},
    ]
    citations = [e for e in events if e["type"] == "text_end"]
    assert citations and citations[0]["citations"][0]["url"] == "https://github.com/example-org/ctxpress"


def test_provider_search_and_citations_persist_in_last_blocks(monkeypatch, lenient_content):
    _reply, _calls, _executed, _events, blocks = _run_search_turn(monkeypatch)

    tool = next(b for b in blocks if b["type"] == "tool")
    assert tool["provider_executed"] is True
    assert tool["tool"] == "web_search"
    assert json.loads(tool["input"]) == {"query": "ctxpress"}
    assert tool["result"].startswith("ctxpress — https://github.com/")
    text = next(b for b in blocks if b["type"] == "text")
    assert text["citations"] == [{"url": "https://github.com/example-org/ctxpress",
                                  "title": "ctxpress", "start": 0, "end": 8}]


def test_failed_provider_search_projects_an_error_result():
    block = _server_tool(status="failed", results=[])
    assert provider_search.server_tool_result(block) == "completed"
    assert provider_search.server_tool_card(block) == {
        "type": "tool", "tool": "web_search", "tool_call_id": "ws_1",
        "provider_executed": True, "input": json.dumps({"query": "ctxpress"}),
        "result": "completed", "is_error": True,
    }


# ── chat path: envelopes, ordered blocks, persistence ──────────────────


def _req():
    from openprogram.runtime.agent.turn_request import TurnRequest

    return TurnRequest(session_id="s1", user_text="search", agent_id="main", source="web",
                       user_msg_id="u1")


def test_chat_envelopes_mark_provider_executed():
    from openprogram.runtime.conversation.event_parsing import agent_event_to_envelope

    block = _server_tool()
    partial = SimpleNamespace(content=[block])
    start = SimpleNamespace(type="message_update", assistant_message_event=SimpleNamespace(
        type="servertool_start", content_index=0, partial=partial))
    end = SimpleNamespace(type="message_update", assistant_message_event=SimpleNamespace(
        type="servertool_end", content_index=0, server_tool=block, partial=partial))

    use = agent_event_to_envelope(start, _req())["data"]["event"]
    result = agent_event_to_envelope(end, _req())["data"]["event"]

    assert use == {"type": "tool_use", "tool": "web_search",
                   "input": json.dumps({"query": "ctxpress"}), "tool_call_id": "ws_1",
                   "provider_executed": True}
    assert result["type"] == "tool_result" and result["provider_executed"] is True
    assert result["is_error"] is False and result["outcome"] == "completed"
    assert result["result"].splitlines() == [
        "ctxpress — https://github.com/example-org/ctxpress", "Docs — https://docs.example.org"]


def test_chat_text_end_forwards_citations_only_when_present():
    from openprogram.runtime.conversation.event_parsing import agent_event_to_envelope

    cited = SimpleNamespace(type="text", text="x", citations=[
        SimpleNamespace(url="https://a.example", title="A", start=0, end=1)])
    plain = SimpleNamespace(type="text", text="x", citations=None)

    def ev(block):
        return SimpleNamespace(type="message_update", assistant_message_event=SimpleNamespace(
            type="text_end", content_index=0, partial=SimpleNamespace(content=[block])))

    assert agent_event_to_envelope(ev(plain), _req()) is None
    assert agent_event_to_envelope(ev(cited), _req())["data"]["event"] == {
        "type": "text_end", "content_index": 0,
        "citations": [{"url": "https://a.example", "title": "A", "start": 0, "end": 1}]}


def test_ordered_blocks_carry_provider_search_and_citations():
    from openprogram.runtime.conversation.turn_runtime.loop_runner import (
        _ordered_block_from_content,
    )

    cited = SimpleNamespace(type="text", text="answer", citations=[
        {"url": "https://a.example", "title": None, "start": None, "end": None}])

    assert _ordered_block_from_content(_server_tool())["provider_executed"] is True
    assert _ordered_block_from_content(cited) == {
        "type": "text", "text": "answer",
        "citations": [{"url": "https://a.example", "title": "", "start": None, "end": None}]}


def test_persisted_reply_keeps_provider_search_block_and_citations():
    from openprogram.runtime.agent import provider_search as ps
    from openprogram.runtime.conversation.turn_runtime.persistence import (
        persist_assistant_message,
    )

    written = []
    db = SimpleNamespace(append_message=lambda sid, msg: written.append(msg))
    card = ps.server_tool_card(_server_tool())
    text = {"type": "text", "text": "answer",
            "citations": [{"url": "https://a.example", "title": "A", "start": 0, "end": 6}]}

    persist_assistant_message(
        db=db, req=_req(), session={}, usage={}, final_text="answer", history=[],
        tool_calls=[], _ordered_blocks=[card, text], _agentic_tool_names=set(),
        _placeholder_inserted=False, cancel_event=None, assistant_msg_id="u1_reply",
        user_msg_id="u1",
    )

    blocks = json.loads(written[0]["extra"])["blocks"]
    assert blocks == [card, text]
    assert blocks[0] == {"type": "tool", "tool": "web_search", "tool_call_id": "ws_1",
                         "provider_executed": True,
                         "input": json.dumps({"query": "ctxpress"}),
                         "result": "ctxpress — https://github.com/example-org/ctxpress\n"
                                   "Docs — https://docs.example.org",
                         "is_error": False}


@pytest.mark.parametrize("supported, override, expected_flag, has_local", [
    (True, {"inherit": True, "web_search": True}, True, False),
    (False, {"inherit": True, "web_search": True}, False, True),
    (True, None, False, True),  # Search switch off: nothing changes
])
def test_chat_turn_resolves_the_search_switch_against_the_model(
    monkeypatch, supported, override, expected_flag, has_local,
):
    from openprogram.models.providers.types import Model
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    _snapshot(monkeypatch, supports_web_search=supported)
    model = Model(id="fake", name="fake", api="openai-responses", provider="openai",
                  base_url="https://example.invalid/v1")
    tools = [_other_tool(), _local_tool([])]
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *a, **k: list(tools))
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *a: tool)
    monkeypatch.setattr("openprogram.runtime.context.prompt_assembly.components.build_system_prompt",
                        lambda *a, **k: "system")
    monkeypatch.setattr("openprogram.programs.tools.planning.goal.chat.instructions",
                        lambda *a: "")
    request = TurnRequest("search-session", "find it", "main", "web", tools_override=override)
    request._execution_revision_id = "test-revision"

    _profile, resolved, *_rest, contract = loop_runner.resolve_agent_runtime(request)

    assert request._provider_web_search is expected_flag
    # The conversation's own turn keeps one tools array: the local tool
    # stays listed whichever side searches, and while the provider runs the
    # search its calls are refused, so the Search switch never rewrites it.
    (local,) = [t for t in resolved if t.name == "web_search"]
    assert "web_search" in {t["name"] for t in contract["tools"]}
    assert (getattr(local, "_unavailable_reason", None) is None) is has_local
