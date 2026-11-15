"""The Goal goes beside the conversation's own turn, never in its prompt.

Kimi Code's two notes: the full one (rules, objective, budget, usage,
verification feedback) when nothing in view says the same, the short one
otherwise. The todo list comes back only after a stretch without todo use.
"""
from __future__ import annotations

import pytest

from openprogram.programs.tools.planning.goal import chat


def _goal(**changes):
    goal = {"goal_id": "g1", "revision": 1, "run_id": "r1", "execution_mode": "chat",
            "status": "active", "text": "ship the release", "branch_id": None,
            "budget": {"max_tokens": 1000}, "usage": {"total_tokens": 100}, "last_reason": ""}
    goal.update(changes)
    return goal


@pytest.fixture
def goal(monkeypatch):
    state = {"goal": _goal(), "todos": []}
    monkeypatch.setattr(chat.goals, "load_goal", lambda _sid: state["goal"])
    monkeypatch.setattr(chat, "todos", lambda _sid, _goal: list(state["todos"]))
    return state


def _next(last_full):
    return chat.goal_note("c1", None, last_full)


def test_the_first_goal_turn_gets_the_full_note(goal):
    text, disclosure, full = _next(None)
    assert full
    assert "An active Goal persists" in text and "update_goal" in text
    assert "ship the release" in text and "Usage so far: 100 tokens" in text
    assert "todos" not in text


def test_the_next_turn_with_the_same_state_gets_the_short_note(goal):
    _text, disclosure, _full = _next(None)
    goal["goal"]["usage"] = {"total_tokens": 300}
    text, _disclosure, full = _next(disclosure)
    assert not full
    assert "ship the release" in text and "Usage so far: 300 tokens" in text
    assert "update_goal" not in text
    assert text.count("Usage so far") == 1


@pytest.mark.parametrize("change", [
    {"text": "ship the release and the notes"},
    {"revision": 2},
    {"last_reason": "the changelog is missing"},
    {"usage": {"total_tokens": 800}},  # the budget crosses 75%
])
def test_a_changed_state_sends_the_full_note_again(goal, change):
    _text, disclosure, _full = _next(None)
    goal["goal"] = _goal(**change)
    assert _next(disclosure)[2]


def test_a_compaction_that_drops_the_full_note_sends_it_again(goal):
    # Nothing in view: the render reports no full note.
    assert _next(None)[2]


def test_no_note_once_the_goal_has_ended_or_works_on_another_branch(goal):
    goal["goal"] = _goal(status="achieved")
    assert _next(None) is None
    goal["goal"] = _goal(branch_id="b2")
    assert chat.goal_note("c1", "b1", None) is None


def test_budget_band():
    assert chat.budget_band(_goal()) == "within"
    assert chat.budget_band(_goal(usage={"total_tokens": 750})) == "nearing"
    assert chat.budget_band(_goal(budget={"max_cost_usd": 10}, usage={"cost_usd": 8, "cost_known": True})) == "nearing"
    assert chat.budget_band(_goal(budget={"max_cost_usd": 10}, usage={"cost_usd": 8})) == "within"


def test_the_todo_list_comes_back_after_ten_turns_without_todo_use(goal):
    goal["todos"] = [{"subject": "write notes", "status": "pending"}]
    assert chat.todo_reminder("c1", None, 9, None) == ""
    reminder = chat.todo_reminder("c1", None, 10, None)
    assert "write notes" in reminder and "[pending]" in reminder
    # Reminded recently: not again yet.
    assert chat.todo_reminder("c1", None, 12, 3) == ""
    assert chat.todo_reminder("c1", None, 20, 10)
    # Not while no Goal is active.
    goal["goal"] = _goal(status="paused")
    assert chat.todo_reminder("c1", None, 20, None) == ""
