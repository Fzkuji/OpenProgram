"""Tool guides reach the model through the agent loop, once per view of them."""
from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.runtime.agent.loop.session import AgentSession
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.programs._authoring import guides
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, Model, TextContent, ToolCall, ToolResultMessage,
)

TOOL = "guided_probe"


@pytest.fixture(autouse=True)
def probe_guides():
    guides.register(TOOL, [
        guides.Guide(TOOL, "How the probe works.", mode="open"),
        guides.Guide(f"{TOOL}.fast", "Fast mode rules.", mode="included", when=lambda args: args.get("fast")),
        guides.Guide(f"{TOOL}.deep", "Deep mode rules.", summary="deep probing",
                     required_for=lambda args: args.get("deep")),
        guides.Guide(f"{TOOL}.recover", "When a probe fails, retry once.", mode="on_error"),
    ])
    yield
    guides._registry.pop(TOOL, None)


def _text(result) -> str:
    """What the model reads for this call: the guides recorded beside the
    result (sent after the group of results), then the tool's own text."""
    from openprogram.models.providers.shared.tool_attachments import record

    entry = record(getattr(result, "details", None))
    own = "".join(part.text for part in result.content if hasattr(part, "text"))
    return (entry["text"] if entry else "") + own


def _result(text="ok", error=False) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=text)], is_error=error)


def _assistant(content) -> AssistantMessage:
    calls = any(isinstance(item, ToolCall) for item in content)
    return AssistantMessage(content=content, api="openai-completions", provider="openai", model="fake",
                            stop_reason="toolUse" if calls else "stop", timestamp=int(time.time() * 1000))


def test_the_loop_delivers_a_guide_once_and_again_after_it_leaves_the_context():
    replies = [_assistant([ToolCall(id=f"c{i}", name=TOOL, arguments={})]) for i in range(2)]
    replies.append(_assistant([TextContent(text="done")]))
    seen: list[list[str]] = []

    def stream_fn(model, context, opts):
        seen.append([_text(m) for m in context.messages if isinstance(m, ToolResultMessage)])
        message = replies[min(len(seen) - 1, len(replies) - 1)]

        async def gen():
            yield EventStart(partial=message)
            yield EventDone(reason="toolUse" if message.stop_reason == "toolUse" else "stop", message=message)
        return gen()

    async def execute(call_id, args, cancel, on_update):
        return _result("probed")
    session = AgentSession(model=Model(id="fake", name="fake", api="openai-completions", provider="openai",
                                       base_url="https://example.invalid/v1"),
                           tools=[AgentTool(name=TOOL, label=TOOL, description="probe",
                                            parameters={"type": "object", "properties": {}}, execute=execute)])
    session._agent.stream_fn = stream_fn
    asyncio.run(session.run("go"))
    first, second = seen[1][0], seen[2][1]
    assert first.startswith(f'{guides.marker(TOOL)}\nHow the probe works.')
    assert f'help("{TOOL}.deep"): deep probing' in first and first.endswith("probed")
    assert second == "probed"


def test_included_and_error_guides_follow_the_call():
    token = guides.begin_loop()
    try:
        assert TOOL in _text(guides.attach(TOOL, {}, _result()))
        assert "Fast mode rules." in _text(guides.attach(TOOL, {"fast": True}, _result()))
        assert "Fast mode rules." not in _text(guides.attach(TOOL, {"fast": True}, _result()))
        assert "retry once" in _text(guides.attach(TOOL, {}, _result("broke", error=True)))
        guides.observe_request([{"content": "summary of earlier turns"}])
        assert "How the probe works." in _text(guides.attach(TOOL, {}, _result()))
    finally:
        guides.end_loop(token)


def test_a_required_guide_is_refused_then_carried_by_the_refusal(monkeypatch):
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.approval import wrap_with_approval
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)
    ran = []

    async def execute(call_id, args, cancel, on_update):
        ran.append(args)
        return _result("deep result")
    req = TurnRequest(session_id="guides", user_text="t", agent_id="main", source="web",
                      permission_mode="bypass", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name=TOOL, label=TOOL, description="probe",
                                        parameters={"type": "object"}, execute=execute), req, lambda event: None)
    token = guides.begin_loop()
    try:
        refused = asyncio.run(tool.execute("c1", {"deep": True}, None, None))
        assert refused.is_error and refused.details["reason_code"] == "GUIDE_REQUIRED" and not ran
        delivered = _text(guides.attach(TOOL, {"deep": True}, refused))
        assert "Deep mode rules." in delivered and "needs its instructions first" in delivered
        guides.observe_request([{"content": delivered}])
        assert _text(asyncio.run(tool.execute("c2", {"deep": True}, None, None))) == "deep result"
    finally:
        guides.end_loop(token)


def test_help_reads_a_guide_by_name_or_tool():
    from openprogram.programs.tools.runtime.help import guide_help as tool

    def guide_help(name):
        return _text(asyncio.run(tool.execute("h", {"name": name}, None, None)))
    token = guides.begin_loop()
    try:
        assert guide_help(f"{TOOL}.deep").startswith(f'{guides.marker(TOOL + ".deep")}\nDeep mode rules.')
        assert guides.known(f"{TOOL}.deep")
        assert "How the probe works." in guide_help(TOOL)
        assert guide_help("nothing_here").startswith("[help error] no guide named")
    finally:
        guides.end_loop(token)


def _activated_tool(name: str):
    from openprogram.programs._host.runtime import function

    @function(name=name, description="Made by the guide.", defer=True)
    def activated() -> str:
        return "ok"
    return activated


def test_a_delivered_guide_names_its_tools_and_loads_nothing():
    """Deferred-tools A4: the guide ends with the line that names its tools
    and the select call that loads them; the result carries no load record,
    so the tools array changes only after the model's own search."""
    from openprogram.programs import loaded_tools
    from openprogram.models.providers.shared.tool_attachments import record
    from openprogram.models.providers.types import ToolResultMessage

    _activated_tool("guide_activated_probe")
    guides.register(TOOL, [guides.Guide(TOOL, "Uses it.", mode="open", activates=("guide_activated_probe",))])
    token = guides.begin_loop()
    try:
        first = guides.attach(TOOL, {}, _result())
    finally:
        guides.end_loop(token)
    message = ToolResultMessage(tool_call_id="c1", tool_name=TOOL, content=first.content,
                                details=first.details, timestamp=0)
    assert loaded_tools([message]) == []
    assert not (first.details or {}).get("loads")
    assert ("Tools for this: `guide_activated_probe`. Load them with "
            "`tool_search select:guide_activated_probe`.") in record(first.details)["text"]


def test_help_names_the_tools_its_guide_teaches():
    _activated_tool("help_activated_probe")
    guides.register(TOOL, [guides.Guide(TOOL, "Uses it.", mode="open", activates=("help_activated_probe",))])
    token = guides.begin_loop()
    try:
        text = guides.help_text(TOOL)
        result = guides.attach("help", {"name": TOOL}, _result())
    finally:
        guides.end_loop(token)
    assert "`tool_search select:help_activated_probe`" in text
    assert not (result.details or {}).get("loads")


def test_outside_an_agent_loop_guides_stay_out_of_the_way():
    assert _text(guides.attach(TOOL, {}, _result())) == "ok"
    assert guides.missing_required(TOOL, {"deep": True}) == []


def test_self_update_reads_its_action_guide_before_installing():
    import openprogram.programs.tools.system.self_update  # noqa: F401 — registers its guides
    token = guides.begin_loop()
    try:
        assert [g.name for g in guides.missing_required("self_update", {"action": "prepare"})] == ["self_update.prepare"]
        assert guides.missing_required("self_update", {}) == []
        opening = _text(guides.attach("self_update", {}, _result()))
        assert 'help("self_update.retry")' in opening and "worktree_create" in opening
    finally:
        guides.end_loop(token)
