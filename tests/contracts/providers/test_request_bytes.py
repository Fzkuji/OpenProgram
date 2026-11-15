"""What a turn sends is what every later request sends again, on every adapter.

A provider reuses its cached prompt only when every byte before the new
message is what an earlier request sent. These tests run real turns
(dispatcher, agent loop, DAG render), serialize every request with the real
adapter of each wire format (``tests/support/request_bytes.py``) and check
that the next turn's first request begins with the whole history of the
previous turn's last request.

Cases marked ``xfail`` are known places where a later render differs from
what the turn sent (``docs/reference/design/context/cache-prefix.html``,
"Implementation status"); the change that fixes one removes its mark.
"""
from __future__ import annotations

import base64

import pytest

from openprogram.models.providers.types import AgentTool, AgentToolResult, ImageContent, TextContent, ToolCall
from tests.support.request_bytes import (
    WIRES,
    assert_extends,
    call,
    install_bench,
    lookup_tool,
    reply,
    text_tool,
)

PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def _failing(arguments):
    return AgentToolResult(content=[TextContent(text="no such key")], is_error=True)


def _with_image(arguments):
    return AgentToolResult(content=[TextContent(text="the screen:"),
                                    ImageContent(data=PNG, mime_type="image/png")])


def _search_listing(arguments):
    return "\n".join(f"tool_{n}: " + "x" * 300 for n in range(150))


def _empty(arguments):
    return AgentToolResult(content=[TextContent(text="")])


def _read_tool() -> AgentTool:
    """A file tool: a call reaching a directory with an AGENTS.md attaches it."""
    async def execute(call_id, arguments, cancel=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"contents of {arguments['file_path']}")])

    return AgentTool(name="read", label="read", description="Read a file",
                     parameters={"type": "object", "properties": {"file_path": {"type": "string"}}},
                     execute=execute)


TOOLS = [lookup_tool(), text_tool("probe", _failing), text_tool("screenshot", _with_image),
         text_tool("tool_search", _search_listing), text_tool("quiet", _empty), _read_tool()]

#: Known differences between what a turn sent and what later turns re-send.
#: None remain: a tool result's ``is_error``, a whole ``tool_search`` result,
#: kept images (``store.session.media``), a long message (never cut) and the
#: instructions sent after a result (``shared.tool_attachments``) all render
#: as the turn sent them.
GAPS: dict = {}


def _case(name, wire):
    gap = GAPS.get((name, wire))
    marks = [pytest.mark.xfail(reason=gap, strict=True)] if gap else []
    return pytest.param(name, wire, marks=marks, id=f"{name}-{wire}")


CASES = ["tool_call", "failed_tool", "tool_image", "user_image", "image_only", "long_paste",
         "large_tool_search", "empty_result", "nested_agents_md"]


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    class Nothing:
        def search(self, query, **_kwargs):
            return ""

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Nothing())


@pytest.mark.parametrize("case,wire", [_case(name, wire) for name in CASES for wire in WIRES])
def test_the_next_turn_resends_what_the_turn_sent(case, wire, monkeypatch, tmp_path):
    bench = install_bench(monkeypatch, tmp_path, wire, TOOLS)
    model = bench.model
    first = {}
    if case == "tool_call":
        rounds = [call(model, 1, key="a"), reply(model, TextContent(text="a is 42"))]
    elif case == "failed_tool":
        rounds = [call(model, 1, name="probe", key="a"), reply(model, TextContent(text="it failed"))]
    elif case == "tool_image":
        rounds = [call(model, 1, name="screenshot", key="s"), reply(model, TextContent(text="I see it"))]
    elif case == "large_tool_search":
        rounds = [call(model, 1, name="tool_search", key="q"), reply(model, TextContent(text="found"))]
    elif case == "empty_result":
        rounds = [call(model, 1, name="quiet", key="q"), reply(model, TextContent(text="nothing"))]
    elif case == "nested_agents_md":
        rounds = [_two_reads(model, tmp_path), reply(model, TextContent(text="read both"))]
    elif case in ("user_image", "image_only"):
        rounds = [reply(model, TextContent(text="a red dot"))]
        first["attachments"] = [{"type": "image", "data": PNG, "media_type": "image/png"}]
    else:
        rounds = [reply(model, TextContent(text="read it"))]
    text = ("\n".join(f"log line {n}: " + "y" * 400 for n in range(120)) if case == "long_paste"
            else "" if case == "image_only" else "go")
    bench.turn(text, *rounds, **first)
    bench.turn("thanks", reply(model, TextContent(text="ok")))

    last_of_turn, next_turn = bench.history(-2), bench.history(-1)
    assert_extends(last_of_turn, next_turn)
    assert len(next_turn) > len(last_of_turn)
    sent = "\n".join(last_of_turn)
    if case in ("tool_image", "user_image", "image_only"):
        # The picture itself is sent again, not a note in its place.
        assert PNG in sent and PNG in "\n".join(next_turn)
    if case == "long_paste":
        assert "log line 119: " in "\n".join(next_turn)
    if case == "empty_result":
        assert "(quiet completed with no output)" in sent
    if case == "nested_agents_md":
        # Two calls reached the directory in one group: its file is sent
        # once, after the results, not inside either result.
        assert sent.count("nested rules") == 1
        assert sent.index("contents of") < sent.index("nested rules")


def _two_reads(model, tmp_path):
    from tests.support.request_bytes import reply

    nested = tmp_path / "elsewhere" / "pkg"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("nested rules", encoding="utf-8")
    calls = [ToolCall(id=f"call_{n}|fc_{n}", name="read", arguments={"file_path": str(nested / f"f{n}.py")})
             for n in (1, 2)]
    return reply(model, *calls, stop="toolUse")


def test_every_adapter_reports_the_body_it_sends(monkeypatch, tmp_path):
    """Each adapter hands its body to the capture point, cut into the
    system text, then the history."""
    from openprogram.models.providers.types import Context, SimpleStreamOptions, UserMessage
    from tests.support.request_bytes import sent, wire_model

    for wire in WIRES:
        model = wire_model(wire)
        request = sent(model, Context(system_prompt="be brief",
                                      messages=[UserMessage(content="hi", timestamp=0)]),
                       SimpleStreamOptions(session_id="s1"))
        units = request.units()
        labels = [unit.label for unit in units]
        assert labels[0] == "system" and labels[-1].startswith("history:user"), (wire, labels)
        assert [name for name, _end, _digest in units[0].sections] == ["prompt"], wire
        assert request.settings()["model"] == model.id


#: Every built-in wire format besides the benches' four, with the provider
#: and endpoint its adapter is built for.
OTHER_APIS = {
    "google-generative-ai": ("google", "https://generativelanguage.googleapis.com/v1beta"),
    "gemini-subscription": ("google-gemini-cli", "https://cloudcode-pa.googleapis.com"),
    "azure-openai-responses": ("azure-openai-responses", "https://x.openai.azure.com/openai/v1"),
}


def _pictures_context(api: str, provider: str):
    """A person's picture, then a tool result's picture, as history renders them."""
    from openprogram.models.providers.types import (
        AssistantMessage, Context, Tool, ToolResultMessage, Usage, UserMessage)

    other = PNG[:-8] + "AAAAAAA="
    return other, Context(system_prompt="s", tools=[
        Tool(name="shot", description="d", parameters={"type": "object", "properties": {}})], messages=[
        UserMessage(content=[TextContent(text="look"), ImageContent(data=PNG, mime_type="image/png")],
                    timestamp=0),
        AssistantMessage(content=[ToolCall(id="call_1|fc_1", name="shot", arguments={})], api=api,
                         provider=provider, model="m1", usage=Usage(), stop_reason="toolUse", timestamp=0),
        ToolResultMessage(tool_call_id="call_1|fc_1", tool_name="shot", timestamp=0,
                          content=[TextContent(text="screen"), ImageContent(data=other, mime_type="image/png")]),
        UserMessage(content="ok", timestamp=0)])


@pytest.mark.parametrize("api", [*(spec["api"] for spec in WIRES.values()), *OTHER_APIS])
def test_every_adapter_sends_the_pictures_history_keeps(api):
    """A kept picture, a person's or a tool's, reaches the body on every
    built-in adapter, not a note in its place."""
    from openprogram.models.providers.catalog.register import register_builtins
    from openprogram.models.providers.types import Model, SimpleStreamOptions
    from tests.support.request_bytes import sent

    register_builtins()
    provider, base_url = OTHER_APIS.get(api) or next(
        (spec["provider"], spec["base_url"]) for spec in WIRES.values() if spec["api"] == api)
    model = Model(id="m1", name="m1", api=api, provider=provider, base_url=base_url,
                  input=["text", "image"], context_window=200_000)
    other, context = _pictures_context(api, provider)
    body = "\n".join(sent(model, context, SimpleStreamOptions(session_id="s1")).history_bytes())
    assert PNG in body and other in body, api


def test_bedrock_sends_the_pictures_history_keeps():
    """Bedrock's body, built offline by its converter (sending needs AWS)."""
    import base64 as b64

    from openprogram.models.providers.adapters.amazon_bedrock.amazon_bedrock import _convert_messages_bedrock
    from openprogram.models.providers.types import Model

    model = Model(id="m1", name="m1", api="bedrock-converse-stream", provider="amazon-bedrock",
                  base_url="", input=["text", "image"], context_window=200_000)
    other, context = _pictures_context(model.api, model.provider)
    images = []
    for message in _convert_messages_bedrock(context, model, "none"):
        for block in message["content"]:
            items = block["toolResult"]["content"] if "toolResult" in block else [block]
            images += [item["image"]["source"]["bytes"] for item in items if "image" in item]
    assert [b64.b64encode(raw).decode() if isinstance(raw, bytes) else raw for raw in images] == [PNG, other]


# Deferred tools (docs/reference/design/function/deferred-tools.html) ------

_CRON = {"name": "cron", "description": "Schedule a job.",
         "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}


def _deferred(name: str, *, server: str = "") -> AgentTool:
    tool = text_tool(name, lambda arguments: f"{name} done")
    setattr(tool, "_defer", True)
    if server:
        setattr(tool, "_mcp_server", server)
    return tool


def _loader(arguments):
    return AgentToolResult(content=[TextContent(text="Loaded 1 deferred tool.")],
                           details={"loads": [{"name": "cron", "definition": _CRON}]})


@pytest.mark.parametrize("wire", list(WIRES))
def test_a_deferred_load_extends_the_request_it_follows(wire, monkeypatch, tmp_path):
    """Searching loads a tool; every later request of the turn and the next
    turn extend the one before. On Anthropic the tool is the array's last
    entry, sent with ``defer_loading`` (the fingerprint leaves it out)."""
    bench = install_bench(monkeypatch, tmp_path, wire,
                          [lookup_tool(), text_tool("tool_search", _loader), _deferred("cron")])
    model = bench.model
    bench.turn("schedule it", call(model, 1, name="tool_search", key="cron"),
               call(model, 2, name="cron", key="daily"), reply(model, TextContent(text="scheduled")))
    bench.turn("thanks", reply(model, TextContent(text="ok")))

    for earlier, later in zip(bench.requests, bench.requests[1:]):
        assert_extends(earlier.history_bytes(), later.history_bytes())
    after_load = bench.requests[1]
    if wire == "anthropic":
        tools = after_load.body["tools"]
        assert tools[-1]["name"] == "cron" and tools[-1]["defer_loading"] is True
        assert [unit.label for unit in after_load.units() if unit.label.startswith("tools:")] == \
            [unit.label for unit in bench.requests[0].units() if unit.label.startswith("tools:")]


@pytest.mark.parametrize("wire", list(WIRES))
def test_an_mcp_server_connecting_mid_conversation_leaves_the_prompt_alone(wire, monkeypatch, tmp_path):
    """The catalog goes beside the message: a server that connects between
    turns adds a line there, and the system prompt and history are the
    bytes the turn before sent."""
    tools = [lookup_tool(), text_tool("tool_search", _loader)]
    bench = install_bench(monkeypatch, tmp_path, wire, tools)
    model = bench.model
    bench.turn("hello", reply(model, TextContent(text="hi")))
    tools.append(_deferred("linear__create_issue", server="linear"))
    bench.turn("file an issue", reply(model, TextContent(text="ok")))

    assert bench.contexts[0].system_prompt == bench.contexts[1].system_prompt
    assert_extends(bench.history(0), bench.history(1))
    last_user = "\n".join(bench.history(1)[len(bench.history(0)):])
    # No catalog was in view before, so the whole catalog is sent once.
    assert "<deferred-tools>" in last_user and "linear__create_issue" in last_user
