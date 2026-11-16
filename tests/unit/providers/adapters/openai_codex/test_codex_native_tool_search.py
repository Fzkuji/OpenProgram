"""A deferred tool loaded mid-conversation leaves the Codex request prefix alone.

The Codex backend renders the tools array ahead of the conversation, and any
change to that array (measured: one tool appended at its end) makes it read
the whole request uncached. The loader is therefore sent as the Responses
client-executed ``tool_search`` tool: the model's search comes back as a
``tool_search_call`` item, its result goes as a ``tool_search_output`` item
carrying the loaded definitions, and the array stays what it was.

The definitions come from the result's load record (``details["loads"]``,
``shared.tool_loads``), written when the tools were loaded: never from the
result text, and never from the tool as it is now.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
from openprogram.models.providers.shared.openai_responses import (
    convert_responses_messages,
    loads_searched_tools_from_input,
    native_tool_search,
    process_responses_stream,
)
from openprogram.models.providers.settings.tool_search_spec import tool_search_form
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

MODEL = Model(id="gpt-5.5", name="gpt-5.5", api="openai-codex", provider="openai-codex",
              base_url="https://example.invalid")

READ = Tool(name="read", description="Read a file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}},
                        "required": ["path"]})
SEARCH = Tool(name="tool_search", description="Load deferred tools.",
              parameters={"type": "object", "properties": {"select": {"type": "string"}},
                          "required": ["select"]})
SCHEDULER = Tool(name="scheduler", description="Manage scheduled jobs.",
                 parameters={"type": "object", "properties": {"action": {"type": "string"}},
                             "required": ["action"]})


def _loads(*tools: Tool) -> dict:
    """The load record ``record_loads`` writes for ``tools``."""
    return {"loads": [{"name": t.name, "definition": {
        "name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools]}


def _search_result_text(tool: Tool) -> str:
    # What _tool_search_by_name returns for one loaded tool.
    line = json.dumps({"name": tool.name, "description": tool.description,
                       "parameters": tool.parameters}, ensure_ascii=False)
    return ("Loaded 1 deferred tool. Their full schemas follow.\n<functions>\n"
            f"<function>{line}</function>\n</functions>")


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api="openai-codex", provider="openai-codex",
                            model="gpt-5.5", timestamp=0)


def _body(messages, tools) -> dict:
    context = Context(system_prompt="You help.", messages=messages, tools=tools)
    items = convert_responses_messages(MODEL, context, include_system_prompt=False,
                                       native_tool_search=native_tool_search(MODEL, context))
    return cx._build_request_body(MODEL, context, {"session_id": "s1"}, items)


def _loaded_conversation(text: str | None = None, details: dict | None = None):
    return [
        UserMessage(content="List my scheduled jobs.", timestamp=0),
        _assistant(ToolCall(id="call_1|tsc_1", name="tool_search",
                            arguments={"select": "select:scheduler"})),
        ToolResultMessage(tool_call_id="call_1|tsc_1", tool_name="tool_search",
                          content=[TextContent(text=text if text is not None
                                               else _search_result_text(SCHEDULER))],
                          details=details if details is not None else _loads(SCHEDULER),
                          timestamp=0),
    ]


def test_loading_a_tool_leaves_the_tools_array_unchanged():
    first = _body([UserMessage(content="List my scheduled jobs.", timestamp=0)], [READ, SEARCH])
    # After the load the split hands the provider the loaded tool too.
    after = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])

    assert after["tools"] == first["tools"]
    assert [t.get("name") or t["type"] for t in after["tools"]] == ["read", "tool_search"]
    assert after["tools"][1]["type"] == "tool_search"
    assert after["tools"][1]["execution"] == "client"
    assert after["instructions"] == first["instructions"]
    # The bytes sent before the load are the start of the request after it.
    assert after["input"][: len(first["input"])] == first["input"]


def test_the_search_and_its_result_carry_the_loaded_definition():
    body = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    call, output = body["input"][1], body["input"][2]
    assert call == {"type": "tool_search_call", "execution": "client", "call_id": "call_1",
                    "status": "completed", "arguments": {"select": "select:scheduler"}}
    assert output == {"type": "tool_search_output", "execution": "client", "call_id": "call_1",
                      "status": "completed", "tools": [{
                          "type": "function", "name": "scheduler",
                          "description": "Manage scheduled jobs.",
                          "parameters": output["tools"][0]["parameters"],
                          "strict": output["tools"][0]["strict"]}]}
    assert output["tools"][0]["parameters"]["properties"] == {"action": {"type": "string"}}


def test_the_output_is_the_same_bytes_as_the_array_entry_it_replaces():
    # The definition from the record renders exactly as the tool would in
    # the array, so moving a tool from the array into an output never
    # changes how the tool itself reads.
    body = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    in_array = cx.convert_responses_tools([SCHEDULER], MODEL.api, MODEL.id)[0]
    assert body["input"][2]["tools"] == [in_array]


@pytest.mark.parametrize("text", [
    "Loaded 1 deferred tool. Their full schemas follow.\n<functions>\n<function>{\"name\": \"sch",
    "[aged] tool_search({\"select\": \"select:scheduler\"}) → loaded tools: scheduler",
    "",
])
def test_a_cut_or_aged_result_sends_the_same_output(text):
    full = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    other = _body(_loaded_conversation(text), [READ, SEARCH, SCHEDULER])
    assert other["input"][2] == full["input"][2]
    assert other["tools"] == full["tools"]


def test_a_schema_change_after_the_load_leaves_the_old_output_alone():
    # The tool's parameters changed after the load (an update, a describe
    # hook): the output keeps the definition the model was first sent.
    changed = Tool(name="scheduler", description="Manage scheduled jobs.",
                   parameters={"type": "object", "properties": {
                       "action": {"type": "string"}, "job_id": {"type": "string"}},
                       "required": ["action"]})
    before = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    after = _body(_loaded_conversation(), [READ, SEARCH, changed])
    assert after["input"] == before["input"]
    (spec,) = after["input"][2]["tools"]
    assert set(spec["parameters"]["properties"]) == {"action"}
    # Offered once, from the search output, not again in the array.
    assert [t.get("name") or t["type"] for t in after["tools"]] == ["read", "tool_search"]


def test_a_tool_no_longer_offered_keeps_its_output():
    # A later turn whose tools no longer include the loaded tool (an agent
    # or policy change): history is not rewritten; calling the tool is
    # refused by the dispatcher like any tool the turn does not have.
    with_tool = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    without = _body(_loaded_conversation(), [READ, SEARCH])
    assert without["input"] == with_tool["input"]
    assert [t.get("name") or t["type"] for t in without["tools"]] == ["read", "tool_search"]


def test_a_result_without_a_load_record_loads_nothing():
    # A failed search, or one that matched nothing, records no load.
    body = _body(_loaded_conversation("No deferred tools matched query 'zz'.", {}),
                 [READ, SEARCH, SCHEDULER])
    assert body["input"][2]["tools"] == []
    assert [t.get("name") or t["type"] for t in body["tools"]] == ["read", "tool_search", "scheduler"]


def test_a_tool_whose_search_result_is_gone_stays_in_the_array():
    # History without the search (compacted away): the tool must still be callable.
    body = _body([UserMessage(content="Again.", timestamp=0)], [READ, SEARCH, SCHEDULER])
    assert [t.get("name") or t["type"] for t in body["tools"]] == ["read", "tool_search", "scheduler"]


def test_without_the_loader_tools_are_plain_functions():
    body = _body(_loaded_conversation(), [READ, SCHEDULER])
    assert [t["type"] for t in body["tools"]] == ["function", "function"]
    assert [i.get("type") for i in body["input"][1:]] == ["function_call", "function_call_output"]


@pytest.mark.parametrize("provider,api", [
    ("amazon-bedrock", "bedrock-converse-stream"),
    ("github-copilot", "anthropic-messages"),
    ("vercel-ai-gateway", "anthropic-messages"),
    ("openai", "openai-responses"),
    ("azure-openai-responses", "azure-openai-responses"),
    ("openai-codex", "openai-responses"),
])
def test_only_a_declared_provider_and_api_search_natively(provider, api):
    assert tool_search_form(provider, api) is None
    model = Model(id="m", name="m", api=api, provider=provider, base_url="https://example.invalid")
    assert not loads_searched_tools_from_input(model)
    context = Context(system_prompt="", messages=[], tools=[READ, SEARCH])
    assert not native_tool_search(model, context)


def test_the_codex_declaration_and_a_model_opt_out():
    assert tool_search_form("openai-codex", "openai-codex") == "responses"
    assert loads_searched_tools_from_input(MODEL)
    opted_out = MODEL.model_copy(update={"tool_search": False})
    assert tool_search_form("openai-codex", "openai-codex", opted_out) is None
    assert not loads_searched_tools_from_input(opted_out)
    assert not loads_searched_tools_from_input(None)


class _Collector:
    def __init__(self):
        self.events = []

    def push(self, event):
        self.events.append(event)


async def _feed(events):
    for event in events:
        yield event


def test_a_tool_search_call_comes_back_as_the_loader_call():
    output = AssistantMessage(content=[], api="openai-codex", provider="openai-codex",
                              model="gpt-5.5", timestamp=0)
    stream = _Collector()
    item = {"type": "tool_search_call", "id": "tsc_9", "call_id": "call_9", "execution": "client",
            "status": "completed", "arguments": {"select": "scheduler"}}
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {**item, "status": "in_progress", "arguments": {}}},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"status": "completed", "usage": {
            "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}},
    ]
    asyncio.run(process_responses_stream(_feed(events), output, stream, MODEL))

    assert [e["type"] for e in stream.events] == ["toolcall_start", "toolcall_end"]
    assert stream.events[1]["tool_call"] == {"type": "toolCall", "id": "call_9|tsc_9",
                                             "name": "tool_search",
                                             "arguments": {"select": "scheduler"}}
    (block,) = output.content
    assert isinstance(block, ToolCall) and block.name == "tool_search"
    assert block.arguments == {"select": "scheduler"}
    assert output.stop_reason == "toolUse"


def test_a_keyword_load_reads_back_the_same_on_the_next_turn():
    """The turn sends the result with its load record; the tool node is
    written from the frame and the next turn renders the record back."""
    from types import SimpleNamespace

    from openprogram.models.providers.types import AgentToolResult
    from openprogram.runtime.conversation.event_parsing import (
        STORED_DETAILS_KEY, STORED_RESULT_KEY, agent_event_to_envelope,
    )

    loaded = [
        Tool(name=f"deferred_{i}", description="Does one deferred thing. " * 20,
             parameters={"type": "object", "properties": {
                 f"field_{j}": {"type": "string", "description": "A field. " * 6}
                 for j in range(4)}})
        for i in range(5)
    ]
    full = "\n".join("<function>" + json.dumps({"name": t.name, "description": t.description,
                                                "parameters": t.parameters}) + "</function>"
                     for t in loaded)
    result = AgentToolResult(content=[TextContent(text=full)], details=_loads(*loaded))

    frame = agent_event_to_envelope(
        SimpleNamespace(type="tool_execution_end", tool_name="tool_search", result=result,
                        is_error=False, outcome="completed", tool_call_id="call_1|tsc_1"),
        SimpleNamespace(session_id="s1", user_msg_id="u1"),
    )
    event = frame["data"]["event"]
    stored_text = event.get(STORED_RESULT_KEY, event["result"])
    stored_details = event[STORED_DETAILS_KEY]

    def turn(text, details):
        return _body([
            UserMessage(content="Find scheduling tools.", timestamp=0),
            _assistant(ToolCall(id="call_1|tsc_1", name="tool_search",
                                arguments={"query": "deferred"})),
            ToolResultMessage(tool_call_id="call_1|tsc_1", tool_name="tool_search",
                              content=[TextContent(text=text)], details=details, timestamp=0),
        ], [READ, SEARCH, *loaded])

    in_turn, next_turn = turn(full, result.details), turn(stored_text, stored_details)
    assert [t["name"] for t in in_turn["input"][2]["tools"]] == [t.name for t in loaded]
    assert next_turn["tools"] == in_turn["tools"]
    assert next_turn["input"] == in_turn["input"]


@pytest.fixture
def _fresh_loader():
    """Outside any turn, nothing loaded, discovery not limited to an Agent."""
    from openprogram.programs import apply_default_deferral
    from openprogram.programs._host.runtime import _allowed_tool_names, _request_loaded

    allowed_token = _allowed_tool_names.set(None)
    loaded_token = _request_loaded.set(frozenset())
    apply_default_deferral()
    yield
    _request_loaded.reset(loaded_token)
    _allowed_tool_names.reset(allowed_token)


def test_tool_search_writes_the_load_record(_fresh_loader):
    from openprogram.programs import get_agent_tool
    from openprogram.programs._host.runtime import _tool_search_execute, current_tool

    result = asyncio.run(_tool_search_execute("c1", {"select": "select:scheduler"}, None, None))
    tool = current_tool(get_agent_tool("scheduler"))
    assert tool is not None
    assert result.details == {"loads": [{"name": "scheduler", "definition": {
        "name": "scheduler", "description": tool.description,
        "parameters": tool.parameters}}]}


def test_a_search_that_loads_nothing_writes_no_record(_fresh_loader):
    from openprogram.programs._host.runtime import _tool_search_execute

    result = asyncio.run(_tool_search_execute(
        "c1", {"select": "zzzzqqqxyzzy"}, None, None))
    assert "loads" not in result.details
    assert result.details == {"note": result.content[0].text}


def test_rendered_history_carries_the_load_record_back():
    from openprogram.runtime.context.session_dag.nodes import render_context
    from openprogram.runtime.context.session_dag.render import render_dag_messages
    from openprogram.state.store.session.nodes import Call, Graph, ROLE_CODE, ROLE_LLM, ROLE_USER

    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="List my jobs.", predecessor=None))
    llm = g.add(Call(role=ROLE_LLM, output="You have none.", predecessor=u.id))
    g.add(Call(role=ROLE_CODE, name="tool_search", caller=llm.id,
               input={"select": "select:scheduler"}, output=_search_result_text(SCHEDULER),
               metadata={"expose": "full", "tool_call_id": "call_1",
                         "details": _loads(SCHEDULER)}))
    ids = render_context(g, head_id=llm.id, frame_entry_seq=-1)
    (result,) = [m for m in render_dag_messages(g, ids, reasoning_for=MODEL)
                 if m.role == "toolResult"]
    assert result.details == _loads(SCHEDULER)


# Deferred-tools A3, A5 ---------------------------------------------------

def test_an_unknown_name_sends_an_empty_output_and_says_why(_fresh_loader):
    """No match or an unknown name: an empty ``tools`` list, as Codex itself
    sends for any failed search, then the search's note as its own item."""
    from openprogram.programs._host.runtime import _tool_search_execute

    result = asyncio.run(_tool_search_execute("c1", {"select": "select:nope"}, None, None))
    body = _body(_loaded_conversation(text=result.content[0].text, details=result.details),
                 [READ, SEARCH])
    assert body["input"][2]["tools"] == []
    (part,) = body["input"][3]["content"]
    assert body["input"][3]["role"] == "user"
    assert "No tool named nope is offered in this conversation" in part["text"]
    assert len(body["input"]) == 4


def _search_for(select: str) -> tuple[list, object]:
    """A turn that asks for a file to be sent and searches ``select`` first."""
    from openprogram.programs._host.runtime import _tool_search_execute

    result = asyncio.run(_tool_search_execute("call_1", {"select": select}, None, None))
    return [
        UserMessage(content="Write hello.txt and send it to me.", timestamp=0),
        _assistant(ToolCall(id="call_1|tsc_1", name="tool_search", arguments={"select": select})),
        ToolResultMessage(tool_call_id="call_1|tsc_1", tool_name="tool_search",
                          content=result.content, details=result.details, timestamp=0),
    ], result


@pytest.mark.parametrize("select", ["select:send_file", "send_file", "select:Send_File"])
def test_searching_a_tool_already_in_the_array_says_it_is_callable(_fresh_loader, select):
    """The model searched for ``send_file``, which the array already holds.
    An empty ``tool_search_output`` alone reads as "no such tool" and the
    reply says the file cannot be sent; the note says to call it."""
    from openprogram.programs import get_agent_tool
    from openprogram.programs._host.runtime import _allowed_tool_names, install_turn_tools

    send_file = get_agent_tool("send_file")
    token = install_turn_tools([send_file, get_agent_tool("tool_search")])
    try:
        messages, result = _search_for(select)
    finally:
        _allowed_tool_names.reset(token)
    spec = Tool(name="send_file", description=send_file.description, parameters=send_file.parameters)
    before = _body(messages[:1], [READ, SEARCH, spec])
    body = _body(messages, [READ, SEARCH, spec])

    # Still a function of the array, which the search did not change.
    assert body["tools"] == before["tools"]
    assert [t.get("name") or t["type"] for t in body["tools"]] == ["read", "tool_search", "send_file"]
    assert result.details == {"note": result.content[0].text}
    call, output, note = body["input"][1:]
    assert call["type"] == "tool_search_call"
    assert output["type"] == "tool_search_output" and output["tools"] == []
    text = note["content"][0]["text"]
    assert note["role"] == "user" and note["content"][0]["type"] == "input_text"
    assert text.startswith("<tool-search-result>\n`send_file` is already callable")
    assert "Call it now" in text
    for wrong in ("not available", "unavailable", "Loaded 0", "not found", "No deferred"):
        assert wrong not in text


def test_the_note_is_the_same_bytes_whatever_the_result_text_becomes(_fresh_loader):
    messages, result = _search_for("select:send_file")
    aged = [*messages[:2], messages[2].model_copy(update={
        "content": [TextContent(text="[aged] tool_search → nothing loaded")]})]
    assert _body(aged, [READ, SEARCH])["input"] == _body(messages, [READ, SEARCH])["input"]


def test_a_load_with_a_tool_already_callable_sends_both(_fresh_loader):
    messages, result = _search_for("select:send_file,scheduler")
    assert [entry["name"] for entry in result.details["loads"]] == ["scheduler"]
    body = _body(messages, [READ, SEARCH, SCHEDULER])
    assert [t["name"] for t in body["input"][2]["tools"]] == ["scheduler"]
    assert "`send_file` is already callable" in body["input"][3]["content"][0]["text"]
    assert "scheduler" not in body["input"][3]["content"][0]["text"]


def test_a_search_that_loads_everything_asked_sends_no_note(_fresh_loader):
    messages, result = _search_for("select:scheduler")
    assert "note" not in result.details
    assert len(_body(messages, [READ, SEARCH, SCHEDULER])["input"]) == 3


def test_a_tool_the_turn_made_resident_is_not_loaded_a_second_time(_fresh_loader):
    """A deferred tool the turn sends with its schema (``as_resident``) is in
    the array: a search for it must not record a load, which would move it
    out of the array and re-read the prefix."""
    from openprogram.programs import as_resident, get_agent_tool
    from openprogram.programs._host.runtime import (
        _allowed_tool_names, _tool_search_execute, install_turn_tools,
    )

    token = install_turn_tools([as_resident(get_agent_tool("scheduler")),
                                get_agent_tool("tool_search")])
    try:
        by_name = asyncio.run(_tool_search_execute("c1", {"select": "select:scheduler"}, None, None))
        by_keyword = asyncio.run(_tool_search_execute("c2", {"select": "scheduled jobs"}, None, None))
    finally:
        _allowed_tool_names.reset(token)
    assert by_name.details == {"note": by_name.content[0].text}
    assert by_name.content[0].text.startswith("`scheduler` is already callable")
    assert "scheduler" not in [e["name"] for e in (by_keyword.details or {}).get("loads", [])]


def test_a_later_scope_does_not_inherit_the_tools_a_turn_made_resident(_fresh_loader):
    """The names a turn made resident belong to that turn's allowed set: a
    scope installed afterwards loads the tool, it is not told it is callable."""
    from openprogram.programs import as_resident, get_agent_tool
    from openprogram.programs._host.runtime import (
        _allowed_tool_names, _tool_search_execute, install_turn_tools,
    )

    token = install_turn_tools([as_resident(get_agent_tool("scheduler")),
                                get_agent_tool("tool_search")])
    _allowed_tool_names.reset(token)
    token = _allowed_tool_names.set({"scheduler", "tool_search"})
    try:
        result = asyncio.run(_tool_search_execute("c1", {"select": "select:scheduler"}, None, None))
    finally:
        _allowed_tool_names.reset(token)
    assert [entry["name"] for entry in result.details["loads"]] == ["scheduler"]
    assert "note" not in result.details


def test_selecting_a_loaded_tool_again_returns_its_definition(_fresh_loader):
    from openprogram.programs._host.runtime import _tool_search_execute, note_request_loaded

    note_request_loaded({"scheduler"})
    result = asyncio.run(_tool_search_execute("c2", {"select": "select:scheduler"}, None, None))
    assert [entry["name"] for entry in result.details["loads"]] == ["scheduler"]
    assert "already" not in result.content[0].text
    body = _body([*_loaded_conversation(),
                  _assistant(ToolCall(id="call_2|tsc_2", name="tool_search",
                                      arguments={"select": "select:scheduler"})),
                  ToolResultMessage(tool_call_id="call_2|tsc_2", tool_name="tool_search",
                                    content=result.content, details=result.details, timestamp=0)],
                 [READ, SEARCH])
    assert [t["name"] for t in body["input"][-1]["tools"]] == ["scheduler"]


def test_a_disconnected_source_leaves_the_output_bytes_alone():
    """The tool is gone from the turn's tools; the output is the bytes the
    load record kept."""
    before = _body(_loaded_conversation(), [READ, SEARCH, SCHEDULER])
    after = _body(_loaded_conversation(), [READ, SEARCH])
    assert after["input"] == before["input"]
    assert after["tools"] == before["tools"]
