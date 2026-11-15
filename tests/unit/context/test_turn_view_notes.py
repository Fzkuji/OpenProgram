"""Notes decided from what the request's rendered history shows.

The date, the plan-mode note, the Goal note and the todo reminder go beside
a turn's message only when the history in view does not already give the
model what they say (``turn_context.turn_view``).
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.runtime.context import turn_context
from openprogram.runtime.conversation import plan_mode


class _Node(SimpleNamespace):
    def is_user(self):
        return self.role == "user"

    def is_code(self):
        return self.role == "code"

    def is_llm(self):
        return self.role == "llm"


def _graph(*turns):
    """``turns``: each a list of entries for one user message, or a tool name
    (a code node) in between."""
    nodes, ids = {}, []
    for n, item in enumerate(turns):
        nid = f"n{n}"
        if isinstance(item, str):
            nodes[nid] = _Node(id=nid, role="code", output="ok", metadata={}, function_name=item)
        else:
            nodes[nid] = _Node(id=nid, role="user", output=f"message {n}", function_name="",
                               metadata={"turn_context": list(item)})
        ids.append(nid)
    return SimpleNamespace(nodes=nodes), ids


def _view(*turns):
    return turn_context.turn_view(*_graph(*turns))


def _date(text):
    return {"kind": "attachment", "id": "date", "text": text}


def _plan(mode):
    return {"kind": "attachment", "id": "plan_mode", "text": f"<plan-mode>{mode}</plan-mode>", "mode": mode}


# The date

def test_the_date_goes_on_the_first_turn_and_when_it_changes():
    today = "Today is Friday, November 13, 2026."
    assert turn_context.date_to_send(today, _view()) == today
    assert turn_context.date_to_send(today, _view([_date(today)], [])) == ""
    assert turn_context.date_to_send(today, _view([_date("Today is Thursday, November 12, 2026.")])) == today
    # A render that did not say what it shows sends the date.
    assert turn_context.date_to_send(today, None) == today


def test_a_compaction_that_drops_the_date_brings_it_back():
    today = "Today is Friday, November 13, 2026."
    # The summary leaves only turns without a date note in view.
    assert turn_context.date_to_send(today, _view([], [])) == today


# Plan mode (Claude Code's schedule)

def _plan_run(turns: int, *, compact_at: int | None = None):
    sent, history = [], []
    for n in range(1, turns + 1):
        if compact_at == n:
            history = []
        text, kind = plan_mode.note_for(True, _view(*history))
        sent.append(kind)
        history.append([_plan(kind)] if kind else [])
    return sent


def test_plan_mode_full_on_entering_then_every_fifth_turn_with_every_fifth_note_full():
    sent = _plan_run(26)
    assert sent[0] == "full"
    assert sent[1:5] == [""] * 4
    assert sent[5] == "sparse"
    assert [sent[n - 1] for n in (11, 16, 21)] == ["sparse"] * 3
    assert sent[25] == "full"


def test_plan_mode_after_a_compaction_sends_the_full_note():
    sent = _plan_run(8, compact_at=4)
    assert sent[3] == "full"


def test_leaving_plan_mode_sends_one_exit_note():
    assert plan_mode.note_for(False, _view([_plan("full")], [])) == (plan_mode.EXIT_TEXT, "exit")
    assert plan_mode.note_for(False, _view([_plan("full")], [_plan("exit")])) == ("", "")
    assert plan_mode.note_for(False, _view()) == ("", "")
    text, kind = plan_mode.note_for(True, _view([_plan("full")], [_plan("exit")]))
    assert kind == "full" and "<plan-mode>" in text


# Todo use

def test_todo_turns_count_from_the_last_todo_call_and_reminder():
    view = _view([], "todo_update", [], [])
    assert view["todo"] == {"turns_since_use": 3, "turns_since_reminder": None}
    view = _view([], [{"kind": "attachment", "id": "todo_reminder", "text": "r"}], [])
    assert view["todo"] == {"turns_since_use": 4, "turns_since_reminder": 2}


def test_the_last_full_goal_note_is_the_baseline():
    full = {"kind": "attachment", "id": "goal", "text": "g", "full": True, "disclosure": {"status": "active"}}
    sparse = {"kind": "attachment", "id": "goal", "text": "s", "full": False, "disclosure": {"status": "x"}}
    assert _view([full], [sparse])["goal"] == {"status": "active"}
    assert _view([sparse])["goal"] is None


def test_new_attachments_render_after_the_sections_and_before_recalled_memory():
    entries = []
    for kind, ident in (("attachment", "memory_prefetch"), ("attachment", "goal"),
                        ("section", "environment"), ("attachment", "plan_mode"),
                        ("attachment", "todo_reminder"), ("attachment", "date")):
        entries = turn_context.put(entries, kind, ident, ident)
    assert [entry["id"] for entry in entries] == [
        "date", "environment", "plan_mode", "goal", "todo_reminder", "memory_prefetch"]


def test_recalled_memory_is_replayed_whatever_the_memory_setting():
    entry = {"kind": "attachment", "id": "memory_prefetch", "text": "<memory-context>x</memory-context>"}
    assert turn_context.shown(entry, turn_context.OWN_PROMPT)
    assert turn_context.shown(entry, turn_context.CONVERSATION)
    # Only a compaction's input while memory is off leaves it out.
    assert not turn_context.shown(entry, turn_context.without_recall(turn_context.OWN_PROMPT))
    assert turn_context.without_recall(turn_context.without_recall("own_prompt")) == "own_prompt:without_recall"
