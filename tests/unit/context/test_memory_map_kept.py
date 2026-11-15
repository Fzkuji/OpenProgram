"""A memory ingest that rewrites MEMORY.md between turns leaves the
conversation's cached prefix alone: the system prompt keeps the memory
section it was recorded with, and the changed map reaches the model beside
the next turn's message, stored on its node so later requests send the same
bytes. These run real turns (dispatcher, agent loop, DAG render) with a
scripted provider, as ``test_turn_prefix_cache`` does.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import Context, TextContent
from openprogram.runtime.agent import turn_request as agent_turn_request
from tests.support.codex_turns import (  # noqa: F401  (fixtures)
    _assert_next_turn_extends,
    _model,
    _model_and_profile,
    _reply,
    _scripted_stream,
    _turn,
    _wire,
    lookup_tool,
    tmp_db,
)

MAP_A = "<memory-context>\n# Memory map\n- `topics/work/` — research projects\n</memory-context>"
MAP_B = ("<memory-context>\n# Memory map\n- `topics/work/` — research projects\n"
         "- `topics/pets.md` — the user's cat is called Quokka-7\n</memory-context>")


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch):
    state = {"map": MAP_A}

    class Memory:
        def system_prompt(self, **_kwargs):
            return state["map"]

        def search(self, query, **_kwargs):
            return ""

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Memory())
    return state


def _sent(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _user_node(db, turn) -> dict:
    return db.get_messages_by_id("c1", [turn.user_msg_id])[0]


def test_a_changed_memory_map_keeps_the_prefix_and_reaches_the_model(tmp_db, lookup_tool, memory, monkeypatch):
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    second = _turn("what is my cat called?", stream, monkeypatch)
    _turn("thanks", stream, monkeypatch)

    wire = [_wire(context) for context in requests]
    # The instructions are the ones the conversation started with, byte for byte.
    assert requests[1].system_prompt == requests[2].system_prompt == requests[0].system_prompt
    assert MAP_A in requests[0].system_prompt and "Quokka-7" not in requests[1].system_prompt
    _assert_next_turn_extends(wire[:2])
    _assert_next_turn_extends(wire[1:])
    # The new map goes beside the message of the turn that first sees it ...
    assert _sent(MAP_B) in wire[1][-1]
    assert _user_node(tmp_db, second).get("memory_map", "").count("Quokka-7") == 1
    # ... and stays there, once, in every later request.
    assert sum(_sent(MAP_B) in item for item in wire[2]) == 1
    assert _sent(MAP_B) not in wire[2][-1]


def test_the_map_is_sent_again_where_its_note_is_not_shown(tmp_db, lookup_tool, memory, monkeypatch):
    """A run that starts from nothing (a scheduled task, a clean sub-agent)
    shows no earlier turn, so the note an earlier turn carried is not what
    its model reads: the current map comes beside its own message."""
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "ok", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    _turn("what is my cat called?", stream, monkeypatch)
    _turn("check in", stream, monkeypatch, history_override=[])

    assert requests[2].system_prompt == requests[0].system_prompt
    assert len(requests[2].messages) == 1
    assert _sent(MAP_B) in _wire(requests[2])[-1]


def test_memory_that_changes_with_the_rest_of_the_prompt_is_rebuilt_in_it(tmp_db, lookup_tool, memory, monkeypatch):
    """When anything else in the prompt changed the cached prefix ends there
    anyway, so the prompt is rebuilt whole with the current memory and the
    turn carries no note."""
    from openprogram.runtime.conversation import turn_runtime as D

    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "you are terse"})
    second = _turn("what is my cat called?", stream, monkeypatch)

    assert MAP_B in requests[1].system_prompt
    assert "memory-map-update" not in _wire(requests[1])[-1]
    assert not _user_node(tmp_db, second).get("memory_map")


def test_turning_memory_off_takes_nothing_back(tmp_db, lookup_tool, memory, monkeypatch):
    """Memory turned off keeps the prefix: the prompt keeps its memory
    section and the earlier note stays in the history (Codex, Claude Code and
    Hermes take nothing back); the next message says the section no longer
    applies, once. The memory leaves at the next compaction."""
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7", "ok", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    _turn("what is my cat called?", stream, monkeypatch)
    memory["map"] = ""
    _turn("thanks", stream, monkeypatch)
    _turn("and now?", stream, monkeypatch)

    wire = [_wire(context) for context in requests]
    assert requests[2].system_prompt == requests[0].system_prompt
    _assert_next_turn_extends(wire[1:3])
    _assert_next_turn_extends(wire[2:4])
    gone = 'section="memory"'
    assert _sent(gone) in wire[2][-1] and "no longer applies" in wire[2][-1]
    assert sum(_sent(gone) in item for item in wire[3]) == 1


def test_a_call_with_its_own_prompt_does_not_render_the_notes(tmp_db, lookup_tool, memory, monkeypatch):
    """``runtime.exec`` and workflows build a prompt holding today's memory;
    an older note after it would contradict it, so they render none."""
    from openprogram.runtime.context.render import _stored_turn_block
    from openprogram.runtime.context.turn_context import CONVERSATION
    from openprogram.state.store import SessionNodeWriter

    stream = _scripted_stream([], [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    second = _turn("what is my cat called?", stream, monkeypatch)
    node = SessionNodeWriter(tmp_db, "c1").load().nodes[second.user_msg_id]

    assert "Quokka-7" not in _stored_turn_block(node)
    assert "Quokka-7" in _stored_turn_block(node, CONVERSATION)


def _resumed_context(db, request: Context, turn, text: str, monkeypatch) -> Context:
    """The first request of ``turn`` resumed from its journal, as
    ``run_loop_blocking`` builds it for a continuation (a Worker restart or
    an approval answered later)."""
    from openprogram.runtime.agent.loop.message_context import _inject_memory_prefetch
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    monkeypatch.setattr(loop_runner, "resolve_agent_runtime", lambda req, **_kw: (
        {"id": "main"}, [], request.system_prompt, request.system_prompt, _model(), None))
    monkeypatch.setattr(loop_runner, "_restore_continuation_runtime", lambda *_args: None)
    monkeypatch.setattr(loop_runner, "_steering_source", lambda *_args, **_kw: (None, {}))
    monkeypatch.setattr(loop_runner, "_agent_loop_config", lambda *_args, **_kw: None)
    built = []

    async def drain(*, context, **_kw):
        built.append(context)
        return "", {}, []

    monkeypatch.setattr(loop_runner, "_drain_agent_events", drain)
    continuation = SimpleNamespace(
        resolved_snapshot={"system_prompt": request.system_prompt, "model": {"id": _model().id}},
        user_message_id=turn.user_msg_id, prior_messages=lambda: [])
    req = agent_turn_request.TurnRequest(session_id="c1", user_text=text, agent_id="main", source="tui")
    loop_runner.run_loop_blocking(req=req, history=[], on_event=lambda _event: None,
                                  cancel_event=None, continuation=continuation)
    messages = list(built[0].messages)
    _inject_memory_prefetch(messages, "", text)
    return Context(system_prompt=built[0].system_prompt, messages=messages)


def test_a_resumed_turn_sends_its_note_as_it_was_sent(tmp_db, lookup_tool, memory, monkeypatch):
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    second = _turn("what is my cat called?", stream, monkeypatch)

    resumed = _resumed_context(tmp_db, requests[1], second, "what is my cat called?", monkeypatch)
    assert _wire(resumed) == _wire(requests[1])


def test_a_turn_sent_with_memory_off_resumes_as_it_was_sent(tmp_db, lookup_tool, memory, monkeypatch):
    """A turn whose memory was off resumes with the same bytes it was sent
    with: the earlier note stays in its history, and the note that memory
    no longer applies stays on its message."""
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    _turn("what is my cat called?", stream, monkeypatch)
    memory["map"] = ""
    third = _turn("thanks", stream, monkeypatch)

    resumed = _resumed_context(tmp_db, requests[2], third, "thanks", monkeypatch)
    assert _wire(resumed) == _wire(requests[2])


def test_a_call_made_inside_a_turn_renders_no_notes(tmp_db, lookup_tool, memory, monkeypatch):
    """A tool, ``runtime.exec`` or an Agent method inside a turn renders the
    conversation under a system prompt of its own, which holds today's map;
    a note from an earlier turn after it would bring back an older one."""
    from openprogram.models.providers.types import ToolCall
    from openprogram.runtime.context.render import _stored_turn_block
    from openprogram.state.store import SessionNodeWriter

    stream = _scripted_stream([], [
        _reply(TextContent(text="ready")), _reply(TextContent(text="Quokka-7")),
        _reply(ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}), stop="toolUse"),
        _reply(TextContent(text="ok")),
    ])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    second = _turn("what is my cat called?", stream, monkeypatch)
    seen = []
    execute = lookup_tool.execute

    async def recording(call_id, arguments, cancel=None, on_update=None):
        node = SessionNodeWriter(tmp_db, "c1").load().nodes[second.user_msg_id]
        seen.append(_stored_turn_block(node))
        return await execute(call_id, arguments, cancel, on_update)

    object.__setattr__(lookup_tool, "execute", recording)
    from openprogram.runtime.permissions.authority import local_owner_authority
    _turn("look a up", stream, monkeypatch, **local_owner_authority())

    assert seen and all("Quokka-7" not in block for block in seen)


def test_an_engine_that_does_not_report_its_notes_still_brings_the_map(tmp_db, lookup_tool, memory, monkeypatch):
    """A context engine that builds its own TurnPrep does not say which note
    its render shows. The turn then cannot rely on the system prompt's map:
    memory back at that map after a note said otherwise is sent again."""
    from openprogram.runtime.context.engine import DefaultContextEngine
    from openprogram.runtime.context.types import TurnPrep

    prepare = DefaultContextEngine.prepare

    def own_prep(self, **kwargs):
        prep = prepare(self, **kwargs)
        return TurnPrep(system_prompt=prep.system_prompt, agent_messages=prep.agent_messages,
                        history_dicts=prep.history_dicts, budget=prep.budget, usage=prep.usage)

    monkeypatch.setattr(DefaultContextEngine, "prepare", own_prep)
    requests: list[Context] = []
    stream = _scripted_stream(requests, [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B
    _turn("what is my cat called?", stream, monkeypatch)
    memory["map"] = MAP_A
    _turn("forget the cat", stream, monkeypatch)

    assert requests[2].system_prompt == requests[0].system_prompt
    assert _sent(MAP_A) in _wire(requests[2])[-1]


def test_the_context_panel_counts_notes_only_where_they_are_sent(tmp_db, lookup_tool, memory, monkeypatch):
    from openprogram.runtime.context.session_stats import compute_breakdown

    stream = _scripted_stream([], [_reply(TextContent(text=text)) for text in ("ready", "Quokka-7", "ok", "ok")])
    _turn("keep this in mind", stream, monkeypatch)
    memory["map"] = MAP_B + "\n" + "- `topics/garden.md` — tomatoes, basil and a lemon tree\n" * 40
    second = _turn("what is my cat called?", stream, monkeypatch)

    def messages_without_the_note() -> tuple[int, int]:
        from openprogram.runtime.context import turn_context

        stored = turn_context.entries(_user_node(tmp_db, second))
        with_note = compute_breakdown("c1")["messages"]
        tmp_db.merge_node_metadata("c1", second.user_msg_id, turn_context.node_metadata(
            turn_context.put(stored, turn_context.SECTION, "memory", "")))
        without = compute_breakdown("c1")["messages"]
        tmp_db.merge_node_metadata("c1", second.user_msg_id, turn_context.node_metadata(stored))
        return with_note, without

    _turn("thanks", stream, monkeypatch)
    sent, unsent = messages_without_the_note()
    assert sent > unsent
    # Memory turned off takes nothing back: the note is still sent.
    memory["map"] = ""
    _turn("thanks again", stream, monkeypatch)
    sent, unsent = messages_without_the_note()
    assert sent > unsent
