"""A finished turn renders, in the next turn, the way its own requests sent it.

The provider caches a request's prefix. The next turn's first request reuses
it only if every byte before the new user message is what the last request
of the previous turn sent. These tests run real turns (dispatcher, agent
loop, DAG render) with a scripted provider, serialize every request with
the real Codex and Anthropic adapters (``tests/support/request_bytes.py``)
and assert the next turn's request starts with the whole of the previous
turn's last request.

The scenario matrix at the end changes one thing between two turns, as
users and the runtime do. Where the prefix still breaks the case is marked
``xfail`` with the gap it shows; the prompt-cache check
(``models/usage/prefix_fingerprint.py``) must name the part that changed.
"""
from __future__ import annotations

import json

import pytest

from openprogram.models.providers.types import AgentToolResult, TextContent, ToolCall
from openprogram.models.usage import prefix_fingerprint
from openprogram.models.usage.context import UsageContext
from openprogram.models.usage.ledger import UsageLedger
from tests.support.request_bytes import (
    assert_extends,
    call,
    install_bench,
    lookup_tool,
    reply,
    sent,
)

MEMORY = "<memory-context>\nThe user's dog is called Pip.\n</memory-context>"
WIRES = ["codex", "anthropic"]


@pytest.fixture(params=WIRES)
def bench(request, monkeypatch, tmp_path):
    return install_bench(monkeypatch, tmp_path, request.param, [lookup_tool()])


def _calls(bench, *keys, start=1):
    return [call(bench.model, n, key=key) for n, key in enumerate(keys, start)]


def _text(bench, text):
    return reply(bench.model, TextContent(text=text))


def _escaped(text: str) -> str:
    return json.dumps(text)[1:-1]


def _count(history: list[str], text: str) -> int:
    return sum(_escaped(text) in item for item in history)


def _assert_next_turn_extends(bench) -> None:
    last, first_next = bench.history(-2), bench.history(-1)
    assert_extends(last, first_next)
    assert len(first_next) > len(last)


def test_the_next_turn_starts_with_the_previous_turns_last_request(bench):
    bench.turn("look up a, then b", *_calls(bench, "a", "b"), _text(bench, "a and b are 42"))
    bench.turn("now c", *_calls(bench, "c", start=3), _text(bench, "c is 42"))
    bench.turn("thanks", _text(bench, "ok"))
    assert len(bench.requests) == 6
    # Inside a turn every request carries the previous one on; across turns
    # the next turn's first request begins with the previous turn's last.
    for earlier, later in ((0, 1), (1, 2), (3, 4), (2, 3), (4, 5)):
        assert_extends(bench.history(earlier), bench.history(later))
    assert bench.contexts[3].system_prompt == bench.contexts[2].system_prompt
    assert [tool.name for tool in bench.contexts[3].tools] == [tool.name for tool in bench.contexts[2].tools]
    # The calls keep their arguments when replayed.
    replayed = [json.loads(item) for item in bench.history(5)
                if '"function_call"' in item or '"tool_use"' in item]
    arguments = [json.loads(item["arguments"]) if "arguments" in item else item["block"]["input"]
                 for item in replayed]
    assert arguments == [{"key": "a"}, {"key": "b"}, {"key": "c"}]


def test_recalled_memory_stays_on_its_turns_message(bench, monkeypatch):
    class Memory:
        def search(self, query, **_kwargs):
            return MEMORY if "dog" in query else ""

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Memory())
    bench.turn("what is my dog called", *_calls(bench, "dog"), _text(bench, "Pip"))
    bench.turn("thanks", _text(bench, "ok"))
    assert _count(bench.history(0), MEMORY) == 1
    _assert_next_turn_extends(bench)
    assert _count(bench.history(-1), MEMORY) == 1


def test_text_before_a_call_stays_in_its_round(bench):
    """The usual reply shape: a line of text, a call, then the answer."""
    model = bench.model
    bench.turn("look up a and b",
               reply(model, TextContent(text="Let me check."),
                     ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}), stop="toolUse"),
               reply(model, TextContent(text="Now b."),
                     ToolCall(id="call_2|fc_2", name="lookup", arguments={"key": "b"}), stop="toolUse"),
               _text(bench, "a and b are 42"))
    bench.turn("thanks", _text(bench, "ok"))
    _assert_next_turn_extends(bench)


def test_calls_made_together_render_together(bench):
    """Two calls in one response go back as one response, then both results."""
    model = bench.model
    bench.turn("look up a and b",
               reply(model, ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}),
                     ToolCall(id="call_2|fc_2", name="lookup", arguments={"key": "b"}), stop="toolUse"),
               reply(model, TextContent(text="Checking c."),
                     ToolCall(id="call_3|fc_3", name="lookup", arguments={"key": "c"}), stop="toolUse"),
               _text(bench, "all 42"))
    bench.turn("thanks", _text(bench, "ok"))
    _assert_next_turn_extends(bench)
    if bench.model.api == "openai-codex":
        kinds = [json.loads(item).get("type") for item in bench.history(-1)]
        assert kinds[1:5] == ["function_call", "function_call", "function_call_output", "function_call_output"]


def test_a_run_note_stays_on_its_turns_message(bench):
    note = "<run-note>Nobody is watching this run.</run-note>"
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"), run_note=note)
    bench.turn("thanks", _text(bench, "ok"))
    assert _count(bench.history(0), note) == 1
    _assert_next_turn_extends(bench)
    assert _count(bench.history(-1), note) == 1


def test_a_resumed_turn_sends_its_run_note_once(bench):
    """A resumed turn renders its message from the node, which already
    carries the note, so the loop is not given it to add again."""
    from types import SimpleNamespace

    from openprogram.models.providers.types import Context
    from openprogram.runtime.agent import turn_request as agent_turn_request
    from openprogram.runtime.agent.loop.message_context import _inject_memory_prefetch
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _continuation_agent_context

    note = "<run-note>Nobody is watching this run.</run-note>"
    result = bench.turn("look up a", _text(bench, "ok"), run_note=note)
    continuation = SimpleNamespace(
        resolved_snapshot={"system_prompt": "you are helpful", "model": {"id": bench.model.id}},
        user_message_id=result.user_msg_id, prior_messages=lambda: [])
    req = agent_turn_request.TurnRequest(session_id="c1", user_text="look up a", agent_id="main", source="tui",
                                         run_note=note)
    context = _continuation_agent_context(req, continuation, db=bench.db, model=bench.model,
                                          tools=[], runtime_contract=None)
    assert context.run_note is None
    messages = list(context.messages)
    _inject_memory_prefetch(messages, context.run_note or "", "look up a")
    resumed = sent(bench.model, Context(system_prompt="", messages=messages)).history_bytes()
    assert [item for item in resumed if "look up a" in item] == [
        item for item in bench.history(0) if "look up a" in item]


def _four_turns(bench):
    for n in range(4):
        bench.turn(f"look up k{n}", call(bench.model, n, key=f"k{n}"), _text(bench, f"k{n} is 42"))


def test_old_tool_results_are_resent_whole_every_turn(bench):
    """No turn rewrites an older result: each next turn extends the last
    request of the turn before (aging is gone; compaction alone replaces
    old results)."""
    _four_turns(bench)
    for last, first_next in ((1, 2), (3, 4), (5, 6)):
        assert_extends(bench.history(last), bench.history(first_next))
    assert not any("[aged]" in item for item in bench.history(-1))
    assert any("k0 is 42" in item or "value of k0" in item for item in bench.history(-1))


def test_a_message_stored_before_its_turn_keeps_what_was_sent_with_it(bench, monkeypatch):
    """The Worker stores the message, then starts the turn: the memory the
    turn recalled and its run note still go onto that message's node."""
    class Memory:
        def search(self, query, **_kwargs):
            return MEMORY if "dog" in query else ""

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Memory())
    note = "<run-note>Nobody is watching this run.</run-note>"
    db = bench.db
    db.create_session("c1", "main", title="t")
    db.append_message("c1", {"id": "u1", "role": "user", "content": "what is my dog called",
                             "timestamp": 1.0, "predecessor": None})
    db.set_head("c1", "u1")
    from openprogram.runtime.conversation.turn_lifecycle import insert_placeholder
    assert insert_placeholder(db, "c1", "u1_reply", "u1", "web")

    bench.turn("what is my dog called", *_calls(bench, "dog"), _text(bench, "Pip"),
               user_msg_id="u1", user_already_persisted=True, run_note=note)
    bench.turn("thanks", _text(bench, "ok"))
    for text in (MEMORY, note):
        assert _count(bench.history(0), text) >= 1
        assert _count(bench.history(-1), text) == 1
    _assert_next_turn_extends(bench)


def test_a_turn_resumed_from_its_journal_renders_as_it_was_sent(bench):
    """A resumed turn stores the cards its journal folds back. They carry the
    mark where each provider response began, as the live turn's cards do, so
    the next turn renders those responses as they were sent and its first
    request still starts with the last request of the resumed turn."""
    from openprogram.runtime.execution.agent_journal import DECISION, TOOL_RESULT, JournalState, apply_journal_record

    model = bench.model
    replies = [
        reply(model, TextContent(text="Let me check."),
              ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}), stop="toolUse"),
        reply(model, TextContent(text="Now b."),
              ToolCall(id="call_2|fc_2", name="lookup", arguments={"key": "b"}), stop="toolUse"),
        reply(model, TextContent(text="a and b are 42")),
    ]
    journal = JournalState(execution_id="run-1")
    for n, answer in enumerate(replies, 1):
        apply_journal_record(journal, {"type": DECISION, "data": {
            "position": 2 * n - 1, "action_id": f"a{n}", "message": answer.model_dump(mode="json"), "turn": {}}})
        for block in answer.content:
            if isinstance(block, ToolCall):
                apply_journal_record(journal, {"type": TOOL_RESULT, "data": {
                    "position": 2 * n, "tool_call_id": block.id, "is_error": False,
                    "result": {"content": [{"type": "text",
                                            "text": f"value of {block.arguments['key']} is 42"}]}}})
    display = journal.display_blocks()
    assert [bool(block.get("round_start")) for block in display] == [True, False, True, False, True]

    first = bench.turn("look up a and b", *replies)
    # Store the reply as a turn resumed from this journal stores it.
    node = bench.db.get_messages_by_id("c1", [first.assistant_msg_id])[0]
    extra = json.loads(node["extra"]) if isinstance(node.get("extra"), str) else dict(node.get("extra") or {})
    extra["blocks"] = display
    bench.db.merge_node_metadata("c1", first.assistant_msg_id, {"extra": json.dumps(extra)})
    bench.turn("thanks", _text(bench, "ok"))
    _assert_next_turn_extends(bench)


def test_a_conversation_that_crosses_midnight_keeps_its_prefix(bench, monkeypatch):
    """The date rides each turn's message, not the instructions: the next
    day's first request begins with the last request of the day before, and
    the model is told the new date beside the new message."""
    import datetime

    from openprogram.runtime.context import components

    day = {"value": datetime.date(2026, 11, 12)}
    monkeypatch.setattr(components, "current_date_note",
                        lambda today=None: day["value"].strftime("Today is %A, %B %d, %Y."))
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    day["value"] = datetime.date(2026, 11, 13)
    bench.turn("thanks", _text(bench, "ok"))

    assert bench.contexts[-1].system_prompt == bench.contexts[0].system_prompt
    assert "Today is" not in bench.contexts[0].system_prompt
    _assert_next_turn_extends(bench)
    thursday, friday = "Today is Thursday, November 12, 2026.", "Today is Friday, November 13, 2026."
    history = bench.history(-1)
    assert _count(history, thursday) == 1
    assert _escaped(friday) in history[-1] and _escaped(thursday) not in history[-1]


# The scenario matrix -------------------------------------------------------

def _between_turns(monkeypatch, target: str, first, second):
    """Make ``target`` (a dotted attribute) answer ``first`` until
    :func:`_switch` is called, then ``second``."""
    state = {"value": first}
    monkeypatch.setattr(target, lambda *_args, **_kwargs: state["value"])
    return lambda: state.update(value=second)


def _l2_section(monkeypatch, first, second):
    """A browser Page section (L2) that appears on the second turn."""
    from openprogram.runtime.context import components

    state = {"value": first}
    monkeypatch.setitem(components._REGISTRY, "L2", [
        *components._REGISTRY["L2"],
        components.ContextComponent("pages", "L2", 50, lambda agent: state["value"])])
    return lambda: state.update(value=second)


def _registered(monkeypatch, name, first, second):
    """Component ``name`` builds ``first`` on the first turn, ``second`` after."""
    from openprogram.runtime.context import components

    state = {"value": first}
    layer = next(layer for layer, bucket in components._REGISTRY.items()
                 if any(component.name == name for component in bucket))
    bucket = [components.ContextComponent(c.name, c.layer, c.order, lambda agent: state["value"], c.condition)
              if c.name == name else c for c in components._REGISTRY[layer]]
    monkeypatch.setitem(components._REGISTRY, layer, bucket)
    return lambda: state.update(value=second)


#: name -> (how the change is made, what the check names, the gap it shows or None)
SCENARIOS = {
    "open_work": (lambda mp: _between_turns(
        mp, "openprogram.runtime.conversation.open_work.prompt_block",
        "", "<open_work>Reply to Ann by Friday.</open_work>"),
        None, None),
    # A Goal starts between turns: its note goes beside the message (B1).
    "goal_active": (lambda mp: _between_turns(
        mp, "openprogram.programs.tools.planning.goal.chat.goal_note",
        None, ("<goal>\nAn active Goal: {\"usage\": 20}\n</goal>", {"status": "active"}, True)),
        None, None),
    # Memory is turned off between turns: what an earlier turn recalled stays
    # in its history (B4).
    "memory_off": (lambda mp: _memory_off_between_turns(mp), None, None),
    # Nobody is behind the next turn: the question tool stays, refused (B2).
    "nobody_answers": (lambda mp: _between_turns(
        mp, "openprogram.runtime.permissions.policy.nobody_answers", False, True),
        None, None),
    "rewind": (lambda mp: _between_turns(
        mp, "openprogram.runtime.conversation.rewind_marks.take_rewind_note",
        "", "The user rewound the conversation."),
        None, None),
    "browser_page": (lambda mp: _l2_section(mp, "", "<pages>example.com</pages>"),
                     None, None),
    "agents_md_edit": (lambda mp: _between_turns(
        mp, "openprogram.runtime.context.agents_md.system_prompt_block",
        "<agents_md>Use tabs.</agents_md>", "<agents_md>Use spaces.</agents_md>"),
        None, None),
    "skill_installed": (lambda mp: _between_turns(
        mp, "openprogram.runtime.context.components._build_skills",
        "<skills>pdf</skills>", "<skills>pdf, docx</skills>"),
        None, None),
    "worktree_created": (lambda mp: _between_turns(
        mp, "openprogram.runtime.context.components.environment_block",
        "<environment>cwd: /repo</environment>", "<environment>cwd: /repo/.worktrees/a</environment>"),
        None, None),
    "memory_changed": (lambda mp: _registered(
        mp, "memory", "<memory>dog: Pip</memory>", "<memory>dog: Pip; cat: Tom</memory>"),
        None, None),
}


def _memory_off_between_turns(monkeypatch):
    class Memory:
        def search(self, query, **_kwargs):
            return MEMORY

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Memory())
    return _between_turns(monkeypatch, "openprogram.state.memory.policy.allowed", True, False)


def _two_turns_with(bench, monkeypatch, tmp_path, change):
    """Two turns with ``change`` made between them; the check of each request."""
    switch = change(monkeypatch)
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    switch()
    bench.turn("thanks", _text(bench, "ok"))
    return _checks(bench, tmp_path)


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_the_check_names_what_changed(bench, monkeypatch, tmp_path, scenario):
    change, cause, _gap = SCENARIOS[scenario]
    checks = _two_turns_with(bench, monkeypatch, tmp_path, change)
    assert [check.break_cause for check in checks[:-1]] == [None] * (len(checks) - 1)
    assert checks[-1].break_cause == cause


@pytest.mark.parametrize("scenario", [
    pytest.param(name, marks=[pytest.mark.xfail(reason=gap, strict=True)] if gap else [])
    for name, (_change, _cause, gap) in SCENARIOS.items()])
def test_one_change_between_two_turns_keeps_the_prefix(bench, monkeypatch, tmp_path, scenario):
    change, _cause, _gap = SCENARIOS[scenario]
    _two_turns_with(bench, monkeypatch, tmp_path, change)
    assert bench.contexts[-1].system_prompt == bench.contexts[0].system_prompt
    _assert_next_turn_extends(bench)


def _checks(bench, tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    lane = UsageContext(session_id="c1", call_kind="chat", agent_id="main")
    return [prefix_fingerprint.settle_check(
                prefix_fingerprint.check_request(request, context=lane, ledger=ledger, now=float(n)),
                None, ledger=ledger)
            for n, request in enumerate(bench.requests)]


def test_a_source_switch_keeps_the_prefix(bench, tmp_path):
    """The same conversation answered from the TUI, then from the App: the
    channel rules in the environment section change (G5). The prompt is
    kept and the new environment goes beside the App turn's message."""
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    bench.turn("thanks", _text(bench, "ok"), source="web")
    assert [check.break_cause for check in _checks(bench, tmp_path)] == [None, None, None]
    assert bench.contexts[-1].system_prompt == bench.contexts[0].system_prompt
    assert _escaped('<system-prompt-update section="environment">') in bench.history(-1)[-1]


def test_entering_and_leaving_plan_mode_keeps_the_prefix(bench, tmp_path):
    """The plan-mode instructions go beside the message (B2'): entering plan
    mode sends the full note, leaving it one exit note, and the system
    prompt and tools never change."""
    from openprogram.runtime.conversation import plan_mode

    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    bench.turn("plan it", _text(bench, "plan"), permission_mode="plan")
    bench.turn("still planning", _text(bench, "plan"), permission_mode="plan")
    bench.turn("go", _text(bench, "ok"))
    assert [check.break_cause for check in _checks(bench, tmp_path)] == [None] * 5
    assert {context.system_prompt for context in bench.contexts} == {bench.contexts[0].system_prompt}
    assert "<plan-mode>" not in bench.contexts[0].system_prompt
    full = plan_mode.full_text().splitlines()[1]
    assert _escaped(full) in bench.history(2)[-1]
    assert _escaped(full) not in bench.history(3)[-1]
    assert _escaped("Plan mode has ended.") in bench.history(4)[-1]
    assert _count(bench.history(4), full) == 1


def test_a_date_is_sent_once_a_day(bench):
    """Two turns on the same day: only the first carries the date (B3)."""
    from openprogram.runtime.context.components import current_date_note

    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    bench.turn("thanks", _text(bench, "ok"))
    _assert_next_turn_extends(bench)
    assert _count(bench.history(-1), current_date_note()) == 1
    assert _escaped(current_date_note()) not in bench.history(-1)[-1]


def test_a_tool_this_turn_cannot_use_stays_listed_and_is_refused(bench, monkeypatch, tmp_path):
    """A question tool with nobody to answer stays in the array (B2): its
    call is refused without asking, and the next turn says once that it can
    be used again."""
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from tests.support.request_bytes import text_tool

    tools = [lookup_tool(), text_tool("ask_user_question", lambda arguments: "asked")]
    monkeypatch.setattr(loop_runner, "_configure_browser_tool", lambda *_a, **_k: (list(tools), False))
    nobody = {"value": True}
    monkeypatch.setattr("openprogram.runtime.permissions.policy.nobody_answers", lambda req: nobody["value"])
    model = bench.model
    bench.turn("ask me", reply(model, ToolCall(id="call_1|fc_1", name="ask_user_question",
                                               arguments={"key": "q"}), stop="toolUse"),
               _text(bench, "ok"))
    nobody["value"] = False
    bench.turn("thanks", _text(bench, "ok"))
    assert [check.break_cause for check in _checks(bench, tmp_path)] == [None] * 3
    assert [tool.name for tool in bench.contexts[0].tools] == [tool.name for tool in bench.contexts[-1].tools]
    assert _escaped("ask_user_question: nobody is there to answer") in bench.history(0)[-1]
    assert any("TOOL_UNAVAILABLE" in item or "carry on without it" in item for item in bench.history(1))
    assert _escaped("Every listed tool can be used again.") in bench.history(-1)[-1]


def test_an_active_goal_sends_its_full_note_then_short_ones(bench, monkeypatch, tmp_path):
    """A Goal never touches the system prompt (B1): the first Goal turn sends
    the full note, the next one the short note."""
    from openprogram.programs.tools.planning.goal import chat

    goal = {"goal_id": "g1", "revision": 1, "run_id": "r1", "execution_mode": "chat", "status": "active",
            "text": "ship it", "budget": {}, "usage": {"total_tokens": 5}, "last_reason": ""}
    monkeypatch.setattr(chat.goals, "load_goal", lambda _sid: goal)
    monkeypatch.setattr(chat, "todos", lambda *_a: [])
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    bench.turn("go on", _text(bench, "ok"))
    assert [check.break_cause for check in _checks(bench, tmp_path)] == [None] * 3
    assert "Goal" not in bench.contexts[0].system_prompt
    assert _escaped("An active Goal persists") in bench.history(0)[-1]
    assert _escaped("<goal-reminder>") in bench.history(-1)[-1]
    assert _count(bench.history(-1), "An active Goal persists") == 1


def test_a_tool_added_between_turns_is_named(bench, monkeypatch, tmp_path):
    """A browser Page opened between turns adds its tool (G3): the tools
    come first in the prompt, so the whole request after them is re-read."""
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from tests.support.request_bytes import text_tool

    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    tools = [lookup_tool(), text_tool("computer_use_agent", lambda arguments: "done")]
    monkeypatch.setattr(loop_runner, "_configure_browser_tool", lambda *_a, **_k: (tools, True))
    bench.turn("open the page", _text(bench, "ok"))
    assert _checks(bench, tmp_path)[-1].break_cause == "tools:computer_use_agent"


def test_a_restart_between_turns_is_not_a_break(bench, monkeypatch, tmp_path):
    """After a Worker restart the prompt a turn sends again (kept, or from a
    saved contract) is no longer known as composed, so it is not cut into
    sections; the same bytes still match the baseline written before."""
    from openprogram.runtime.context import components

    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    sent_before = len(bench.requests)
    bench.turn("thanks", _text(bench, "ok"))
    lane = UsageContext(session_id="c1", call_kind="chat", agent_id="main")

    def checked(requests, ledger, start):
        return [prefix_fingerprint.settle_check(
                    prefix_fingerprint.check_request(request, context=lane, ledger=ledger, now=float(n)),
                    None, ledger=ledger)
                for n, request in enumerate(requests, start)]

    checked(bench.requests[:sent_before], UsageLedger(db_path=tmp_path / "usage.db"), 0)
    monkeypatch.setattr(components, "_RECENT_PARTS", {})
    monkeypatch.setattr(components, "_RECENT_SECTIONS", {})
    after = checked(bench.requests[sent_before:], UsageLedger(db_path=tmp_path / "usage.db"), sent_before)
    assert [check.break_cause for check in after] == [None] * len(after)


# Notes beside the message ----------------------------------------------------

def test_a_changed_section_is_sent_once_beside_its_turns_message(bench, monkeypatch):
    """open_work changes between turns: the prompt is kept, its whole new
    text goes beside the next message, and later turns render that note on
    the message it was sent with and send no other."""
    switch = _between_turns(monkeypatch, "openprogram.runtime.conversation.open_work.prompt_block",
                            "", "<open_work>Reply to Ann by Friday.</open_work>")
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    switch()
    bench.turn("thanks", _text(bench, "ok"))
    bench.turn("and now?", _text(bench, "ok"))

    assert bench.contexts[-1].system_prompt == bench.contexts[0].system_prompt
    _assert_next_turn_extends(bench)
    assert _count(bench.history(-1), "Reply to Ann by Friday.") == 1
    assert _escaped("Reply to Ann by Friday.") not in bench.history(-1)[-1]


def test_a_shell_change_is_announced_once_across_a_restart(bench, monkeypatch):
    """The shell the conversation was told about stays in its prompt. A new
    shell (Git installed, the sandbox turned on) after a Worker restart is
    told once, beside the next message, as the whole environment section."""
    from openprogram.runtime.context import components

    shell = {"value": " · Shell: bash (/bin/bash)"}
    monkeypatch.setattr(components, "_command_shell_note", lambda env: shell["value"])
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    monkeypatch.setattr(components, "_RECENT_PARTS", {})
    monkeypatch.setattr(components, "_RECENT_SECTIONS", {})
    shell["value"] = " · Shell: Windows PowerShell 5.1 (powershell.exe)"
    bench.turn("thanks", _text(bench, "ok"))
    bench.turn("and now?", _text(bench, "ok"))

    assert bench.contexts[-1].system_prompt == bench.contexts[0].system_prompt
    assert "bash (/bin/bash)" in bench.contexts[0].system_prompt
    _assert_next_turn_extends(bench)
    assert _count(bench.history(-1), "Windows PowerShell 5.1 (powershell.exe)") == 1


def test_a_rewind_note_stays_on_a_message_that_is_sent_again(bench, monkeypatch):
    """The rewind note is taken onto the turn's message when it is stored,
    not when a request is built: a turn that fails after that and is sent
    again with the same message still tells the model."""
    from openprogram.runtime.conversation import rewind_marks

    note = {"value": "The user rewound the conversation."}

    def take(_session_id):
        text, note["value"] = note["value"], ""
        return text

    monkeypatch.setattr(rewind_marks, "take_rewind_note", take)
    first = bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    assert _escaped("The user rewound the conversation.") in bench.history(0)[-1]
    # The same message is sent again (a retry after a failure).
    bench.turn("look up a", _text(bench, "42"), user_msg_id=first.user_msg_id,
               user_already_persisted=True)
    assert _escaped("The user rewound the conversation.") in bench.history(-1)[-1]


def test_a_check_in_does_not_write_the_conversations_prompt(bench, monkeypatch):
    """Only the conversation's own turn records its branch's prompt; another
    turn on the conversation (a background check-in) builds its own."""
    from openprogram.runtime.context.system_prompt_node import NODE_NAME, _name_of

    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    switch = _between_turns(monkeypatch, "openprogram.runtime.conversation.open_work.prompt_block",
                            "", "<open_work>Reply to Ann by Friday.</open_work>")
    switch()
    bench.turn("check in", _text(bench, "ok"), background=True)
    recorded = [node for node in bench.db.get_nodes("c1") if _name_of(node) == NODE_NAME]
    assert len(recorded) == 1
    assert "Reply to Ann" in bench.contexts[-1].system_prompt


def test_system_and_tools_bytes_never_change_on_one_model(bench, monkeypatch):
    """Anthropic's preserved-thinking models refuse a request whose system
    prompt was rebuilt (``system_rerendered``) or whose tools changed
    (``tool_set_changed``): across plan mode in and out, a Goal starting, a
    turn nobody answers and memory turned off, every request on one model
    sends the same system and tools bytes."""
    from openprogram.programs.tools.planning.goal import chat

    goal = {"value": None}
    monkeypatch.setattr(chat.goals, "load_goal", lambda _sid: goal["value"])
    monkeypatch.setattr(chat, "todos", lambda *_a: [])
    nobody = {"value": False}
    monkeypatch.setattr("openprogram.runtime.permissions.policy.nobody_answers", lambda req: nobody["value"])
    bench.turn("look up a", *_calls(bench, "a"), _text(bench, "42"))
    bench.turn("plan it", _text(bench, "plan"), permission_mode="plan")
    goal["value"] = {"goal_id": "g1", "revision": 1, "run_id": "r1", "execution_mode": "chat",
                     "status": "active", "text": "ship it", "budget": {}, "usage": {}, "last_reason": ""}
    bench.turn("work on the goal", _text(bench, "ok"))
    nobody["value"] = True
    bench.turn("scheduled", _text(bench, "ok"))
    monkeypatch.setattr("openprogram.state.memory.policy.allowed", lambda **_k: False)
    bench.turn("thanks", _text(bench, "ok"))

    def fixed(request):
        return [unit.text for unit in request.units() if unit.label.split(":")[0] in ("system", "tools")]

    assert all(fixed(request) == fixed(bench.requests[0]) for request in bench.requests)


def test_a_forked_sub_agent_shares_its_callers_cached_prefix(bench, tmp_path):
    """``agent(start_from="fork")`` starts a sub-agent from the caller's node
    on the caller's own Agent. Its first request must extend the caller's
    last one: same settings, tools and system bytes, then the caller's
    history up to the fork point, so the provider's cached prefix is reused
    (Claude Code's fork reuses the exact rendered bytes the same way)."""
    first = bench.turn("look up a, then b", *_calls(bench, "a", "b"), _text(bench, "a and b are 42"))
    caller_last = len(bench.requests) - 1
    bench.turn("check a again in a fork", *_calls(bench, "a", start=3), _text(bench, "still 42"),
               source="agent_spawn", branch_from=first.assistant_msg_id, advance_head=False)
    caller = prefix_fingerprint.fingerprint(bench.requests[caller_last])
    fork = prefix_fingerprint.fingerprint(bench.requests[caller_last + 1])
    assert prefix_fingerprint.first_break(caller, fork) == (None, None)
    shared = len(caller["units"])
    assert [unit[0] for unit in fork["units"][:shared]] == [unit[0] for unit in caller["units"]]
    assert len(fork["units"]) > shared
    assert fork["settings"] == caller["settings"]
    # The check on the fork's own lane, seeded from the caller's, finds no break.
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    lane = UsageContext(session_id="c1", call_kind="chat", agent_id="main")
    checks = [prefix_fingerprint.settle_check(
                  prefix_fingerprint.check_request(request, context=lane, ledger=ledger, now=float(n)),
                  None, ledger=ledger)
              for n, request in enumerate(bench.requests[:caller_last + 2])]
    assert checks[-1].break_cause is None
