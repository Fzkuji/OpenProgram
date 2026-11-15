"""A tool the conversation's turn cannot use stays in its array, refused.

The conversation's own turn keeps one tools array for the epoch (Codex,
Hermes): what depends on the turn (where it came from, who answers, the
memory setting) refuses the call instead of taking the tool out, and asks
nobody anything.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openprogram.runtime.conversation.turn_runtime import loop_runner


def _req(**changes):
    values = dict(session_id="c1", source="web", goal_verification=None, background=False,
                  advance_head=True, spawned_from_session=None, speaker_id="owner")
    values.update(changes)
    return SimpleNamespace(**values)


def _tool(name):
    async def execute(*_args, **_kwargs):
        raise AssertionError("an unavailable tool must not run")

    async def preflight(*_args, **_kwargs):
        raise AssertionError("an unavailable tool asks nothing")

    return SimpleNamespace(name=name, description="", parameters={}, execute=execute,
                           _permission_preflight=preflight, _interaction_manifest=lambda *_a: {},
                           _permission_managed=True)


def test_only_the_conversations_own_turn_pins_its_tools():
    assert loop_runner.pins_tools(_req())
    assert not loop_runner.pins_tools(_req(goal_verification={"id": "v"}))
    assert not loop_runner.pins_tools(_req(background=True))
    assert not loop_runner.pins_tools(_req(source="self_update_verify"))


def test_turn_availability_names_what_this_turn_cannot_use(monkeypatch):
    monkeypatch.setattr("openprogram.runtime.permissions.policy.nobody_answers", lambda req: True)
    monkeypatch.setattr("openprogram.state.memory.policy.allowed", lambda **_k: False)
    monkeypatch.setattr("openprogram.runtime.permissions.identity.outside_text", lambda req: False)
    from openprogram.programs.tools.knowledge.memory import MEMORY_TOOL_NAMES
    from openprogram.runtime.permissions.attended import ASK_TOOLS
    names = ["chat_send", "read", *sorted(ASK_TOOLS), *sorted(MEMORY_TOOL_NAMES)]
    out = loop_runner.turn_availability(_req(), [_tool(name) for name in names])
    assert "read" not in out
    assert out["chat_send"] == "this message did not come from a chat channel"
    assert all(name in out for name in ASK_TOOLS)
    assert all(out[name] == "memory is turned off for this conversation" for name in MEMORY_TOOL_NAMES)


def test_a_refused_tool_keeps_its_definition_and_asks_nobody():
    tool = _tool("chat_send")
    refused = loop_runner.refused_while_unavailable(tool, "this message did not come from a chat channel")
    assert (refused.name, refused.description, refused.parameters) == (tool.name, tool.description, tool.parameters)
    assert refused._permission_preflight is None and refused._interaction_manifest is None
    result = asyncio.run(refused.execute("call_1", {"text": "hi"}))
    assert result.is_error
    assert result.details["reason_code"] == "TOOL_UNAVAILABLE"
    assert result.details["execution_started"] is False
    assert "did not come from a chat channel" in result.content[0].text


def test_the_availability_section_lists_tools_by_name():
    from openprogram.runtime.context import turn_context
    text = turn_context.availability_text({"web_search": "b", "chat_send": "a"})
    assert text == "- chat_send: a\n- web_search: b"
    assert turn_context.availability_text({}) == ""
    note = turn_context.section_note(turn_context.TOOL_AVAILABILITY, text)
    assert "cannot be used now" in note and text in note
    assert "can be used again" in turn_context.section_note(turn_context.TOOL_AVAILABILITY, "")
