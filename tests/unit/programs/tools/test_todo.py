"""Unit tests for the task tools and the store they share with todos.

All calls go through the _impl functions (the @function binding object
is not callable) with storage redirected to a tmp path. No LLM requests.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from openprogram.programs.tools.planning.todo import shared
from openprogram.programs.tools.planning.todo.task_list.task_list import _task_list_impl
from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
from openprogram.programs.tools.planning.todo.task_step.task_step import _task_step_impl


@pytest.fixture
def board(monkeypatch, tmp_path):
    """Session context + todos.json redirected to a tmp file."""
    monkeypatch.setattr(shared, "current_session_id", lambda: "sess1")
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")
    # The selected branch, without a session database behind it.
    monkeypatch.setattr(shared, "_selected_branch", lambda sid: "main")
    monkeypatch.setattr(shared, "_parent_branch", lambda sid, bid: None)
    monkeypatch.setattr(shared, "notify_goal", lambda sid: None)
    return tmp_path / "todos.json"


@pytest.fixture
def frames(monkeypatch):
    """The ``todos_update`` frames the store sends."""
    sent = []
    from openprogram.runtime import interface_link
    monkeypatch.setattr(interface_link, "broadcast", lambda frame: sent.append(json.loads(json.dumps(frame))))
    return sent


@contextmanager
def turn(reply_id, *, source="web", agent_id="main"):
    """Run the calls inside as one turn: its assistant reply id and request."""
    from openprogram.state.store import _current_turn_id
    from openprogram.runtime.agent.turn_request import reset_turn_request, set_turn_request
    token = _current_turn_id.set(reply_id)
    req_token = set_turn_request(SimpleNamespace(source=source, agent_id=agent_id))
    try:
        yield
    finally:
        reset_turn_request(req_token)
        _current_turn_id.reset(token)


def _branch(board, branch_id="main"):
    """One branch's ``{tasks, todos, plans}`` as stored in todos.json (version 4)."""
    blob = json.loads(board.read_text())
    assert blob["version"] == 4
    return blob["branches"][branch_id]


def _steps(board, branch_id="main"):
    return [(t["id"], t["status"]) for t in _branch(board, branch_id)["todos"]]


def _segments(board, branch_id="main"):
    return [(p["anchor_id"], p["plan_id"], p["state"], [(t["id"], t["status"]) for t in p["todos"]])
            for p in _branch(board, branch_id)["plans"]]


def _text(result) -> str:
    return result if isinstance(result, str) else result.content[0].text


def _effect(result):
    return None if isinstance(result, str) else result.details["task"]


FIVE = ["See how the list renders", "Add fold state", "Restyle", "Add tests", "Run affected tests"]


# --- task_start --------------------------------------------------------------


def test_start_writes_the_task_its_steps_and_the_segment(board):
    with turn("m1_reply"):
        out = _task_start_impl("Fold the agent list", "Finished agents fold into one row.", FIVE)
    assert _text(out) == ("Task T1 started: Fold the agent list\n"
                          "Task: 0 of 5 done; now step 1 See how the list renders; next step 2 Add fold state")
    assert _effect(out) == {"task": "T1", "step": "1"}
    stored = _branch(board)
    (task,) = stored["tasks"]
    assert (task["id"], task["title"], task["purpose"], task["status"], task["advanced"], task["turn"]) == (
        "T1", "Fold the agent list", "Finished agents fold into one row.", "open", False, "m1_reply")
    assert "scope" not in task and task["ended_at"] is None
    assert [(t["id"], t["subject"], t["status"], t["plan"]) for t in stored["todos"]] == [
        (str(n + 1), FIVE[n], "in_progress" if n == 0 else "pending", "T1") for n in range(5)]
    (segment,) = stored["plans"]
    assert {k: segment[k] for k in ("anchor_id", "plan_id", "scope", "title", "purpose", "state", "outcome",
                                    "advanced", "current", "earlier", "ended_at")} == {
        "anchor_id": "m1_reply", "plan_id": "T1", "scope": "", "title": "Fold the agent list",
        "purpose": "Finished agents fold into one row.", "state": "running", "outcome": "",
        "advanced": False, "current": "1", "earlier": [], "ended_at": None}
    assert segment["todos"][0] == {"id": "1", "subject": FIVE[0], "status": "in_progress"}
    assert segment["started_at"] == segment["created_at"]


def test_a_title_alone_opens_a_task_without_steps(board):
    with turn("m1_reply"):
        out = _task_start_impl("Look around")
    assert _text(out) == "Task T1 started: Look around"
    assert _effect(out) == {"task": "T1"}
    assert _branch(board)["todos"] == [] and _branch(board)["plans"][0]["current"] is None


def test_a_title_is_cut_and_required(board):
    with turn("m1_reply"):
        assert "title required" in _task_start_impl("  ")
        _task_start_impl("x" * 60)
    assert len(_branch(board)["tasks"][0]["title"]) == shared.MAX_TITLE


def test_more_than_seven_steps_change_nothing(board):
    with turn("m1_reply"):
        out = _task_start_impl("Too long", steps=[f"s{n}" for n in range(8)])
    assert isinstance(out, str) and out.startswith("Nothing was changed")
    assert not board.exists()


def test_no_session_context(monkeypatch):
    monkeypatch.setattr(shared, "current_session_id", lambda: None)
    assert "no active session context" in _task_start_impl("a")
    assert "no active session context" in _task_step_impl(next=True)
    assert "no active session context" in _task_list_impl()


# --- task_step ---------------------------------------------------------------


def test_next_finishes_the_current_step_and_starts_the_following_one(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
        out = _task_step_impl(next=True, result="list is in agent-list.tsx")
    assert _text(out) == ("Step 1 done: See how the list renders\n"
                          "Task: 1 of 5 done; now step 2 Add fold state; next step 3 Restyle")
    assert _effect(out) == {"task": "T1", "step": "2"}
    stored = _branch(board)
    assert stored["tasks"][0]["advanced"] is True
    assert stored["todos"][0]["result"] == "list is in agent-list.tsx"
    assert _steps(board)[:3] == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]
    (segment,) = stored["plans"]
    assert segment["advanced"] is True and segment["current"] == "2"
    assert segment["todos"][0] == {"id": "1", "subject": FIVE[0], "status": "completed",
                                   "result": "list is in agent-list.tsx"}


def test_step_jumps_out_of_order(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
        out = _task_step_impl(step=4)
        assert _effect(out) == {"task": "T1", "step": "4"}
        assert _steps(board) == [("1", "completed"), ("2", "pending"), ("3", "pending"),
                                 ("4", "in_progress"), ("5", "pending")]
        # The next waiting step follows, whatever its place.
        _task_step_impl(next=True)
        assert _steps(board)[1] == ("2", "in_progress")
        assert "steps 1 to 5, not 9" in _task_step_impl(step=9)


def test_add_appends_and_drop_leaves_the_count(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE[:3])
        out = _task_step_impl(add=["Write docs"], drop=[3])
    # Neither moved the reply to another step.
    assert _effect(out) is None
    assert _text(out) == "Task: 0 of 3 done; now step 1 See how the list renders; next step 2 Add fold state"
    assert _steps(board) == [("1", "in_progress"), ("2", "pending"), ("3", "dropped"), ("4", "pending")]
    assert _branch(board)["todos"][3]["plan"] == "T1"
    assert _branch(board)["tasks"][0]["advanced"] is False


def test_dropping_the_current_step_starts_the_next_one(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE[:3])
        out = _task_step_impl(drop=[1])
    assert _effect(out) == {"task": "T1", "step": "2"}
    assert _steps(board) == [("1", "dropped"), ("2", "in_progress"), ("3", "pending")]


def test_a_result_alone_goes_on_the_step_that_finished_last(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE[:3])
        _task_step_impl(next=True)
        out = _task_step_impl(result="kept per conversation")
    assert _effect(out) is None
    todos = _branch(board)["todos"]
    assert todos[0]["result"] == "kept per conversation" and "result" not in todos[1]


def test_one_call_applies_next_then_add(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=["only"])
        out = _task_step_impl(next=True, result="done it", add=["one more"])
    # The last step finished, then the added one started.
    assert _steps(board) == [("1", "completed"), ("2", "in_progress")]
    assert _effect(out) == {"task": "T1", "step": "2"}
    assert _branch(board)["todos"][0]["result"] == "done it"


def test_outcome_ends_the_task(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE[:3])
        _task_step_impl(next=True)
        out = _task_step_impl(outcome="finished agents fold into one row", result="state kept")
    assert _effect(out) == {"task": None}
    assert _text(out).endswith("Task 'Fold' finished: finished agents fold into one row")
    stored = _branch(board)
    assert _steps(board) == [("1", "completed"), ("2", "completed"), ("3", "dropped")]
    assert stored["todos"][1]["result"] == "state kept"
    task = stored["tasks"][0]
    assert (task["status"], task["outcome"]) == ("done", "finished agents fold into one row")
    (segment,) = stored["plans"]
    assert (segment["state"], segment["outcome"], segment["current"]) == (
        "done", "finished agents fold into one row", None)
    assert segment["ended_at"] is not None
    # The task is closed: another call finds none.
    with turn("m1_reply"):
        assert _task_step_impl(next=True).startswith("No task is open")


def test_without_an_open_task_a_step_call_is_a_hint(board):
    with turn("m1_reply"):
        out = _task_step_impl(next=True)
        assert isinstance(out, str) and out.startswith("No task is open") and "error" not in out
        assert _task_step_impl().startswith("Nothing was changed")
    assert not board.exists()


def test_a_task_that_was_never_advanced_shows_no_progress(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
        _task_step_impl(add=["later"])
    assert _branch(board)["plans"][0]["advanced"] is False


# --- a second task in the same reply ----------------------------------------


def test_a_second_task_displaces_an_unfinished_one(board):
    with turn("m1_reply"):
        _task_start_impl("Fold the agent list", steps=FIVE)
        for _ in range(3):
            _task_step_impl(next=True)
        out = _task_start_impl("Rename the panel", steps=["a", "b"])
    assert _text(out).splitlines()[0] == "Closed 'Fold the agent list' at 3/5; 2 open steps dropped"
    assert _effect(out) == {"task": "T2", "step": "6"}
    stored = _branch(board)
    assert [(t["id"], t["status"]) for t in stored["tasks"]] == [("T1", "displaced"), ("T2", "open")]
    # The board drops what was left; nothing of the displaced task is ticked.
    assert _steps(board)[:5] == [("1", "completed"), ("2", "completed"), ("3", "completed"),
                                 ("4", "dropped"), ("5", "dropped")]
    # Its segment keeps the task as it stood.
    first, second = stored["plans"]
    assert (first["state"], first["current"]) == ("displaced", "4") and first["ended_at"] is not None
    assert [t["status"] for t in first["todos"]] == ["completed"] * 3 + ["in_progress", "pending"]
    assert (second["plan_id"], second["state"], second["current"]) == ("T2", "running", "6")


def test_a_second_task_after_every_step_was_done_closes_the_first_as_done(board):
    with turn("m1_reply"):
        _task_start_impl("First", steps=["a", "b"])
        _task_step_impl(next=True)
        _task_step_impl(next=True)
        out = _task_start_impl("Second")
    assert _text(out).splitlines()[0] == "Closed 'First' (2/2 done)"
    assert _branch(board)["tasks"][0]["status"] == "done"
    assert _segments(board)[0][2] == "done"


def test_a_task_displaced_in_a_later_reply_keeps_its_earlier_segment(board):
    with turn("m1_reply"):
        _task_start_impl("First", steps=["a", "b", "c"])
        _task_step_impl(next=True)
    with turn("m2_reply"):
        _task_start_impl("Second")
    stored = _branch(board)
    assert stored["tasks"][0]["status"] == "displaced"
    assert _steps(board) == [("1", "completed"), ("2", "dropped"), ("3", "dropped")]
    # The earlier reply's segment is as that reply left it.
    assert _segments(board) == [
        ("m1_reply", "T1", "running", [("1", "completed"), ("2", "in_progress"), ("3", "pending")]),
        ("m2_reply", "T2", "running", []),
    ]


# --- the end of a turn -------------------------------------------------------


def test_a_normal_end_finishes_the_current_step_and_a_finished_task(board, frames):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=["a", "b"])
        _task_step_impl(next=True)
    frames.clear()
    segment = shared.close_at_turn_end("sess1", "m1_reply")
    assert segment["state"] == "done" and segment["current"] is None and segment["outcome"] == ""
    assert _steps(board) == [("1", "completed"), ("2", "completed")]
    assert _branch(board)["tasks"][0]["status"] == "done"
    assert [f["data"]["changed"][0]["state"] for f in frames] == ["done"]


def test_a_normal_end_with_steps_left_carries_the_task(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
        _task_step_impl(next=True)
    segment = shared.close_at_turn_end("sess1", "m1_reply")
    assert (segment["state"], segment["current"]) == ("carried", "2")
    assert _branch(board)["tasks"][0]["status"] == "open"
    # The reply ended in the middle of step 2 (it asked something, or it
    # waits): nothing says the step is done, so it is not ticked.
    assert _steps(board)[:3] == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]


def test_a_goal_turn_that_ends_on_the_last_step_does_not_finish_it(board, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    goal = {"goal_id": "g1", "revision": 2}
    monkeypatch.setattr(chat, "current_identity", lambda: dict(goal))
    with turn("m1_reply"):
        _task_start_impl("Migrate", steps=["a", "b"])
        _task_step_impl(next=True)
    # A Goal's turn ends whenever the model stops calling tools.
    segment = shared.close_at_turn_end("sess1", "m1_reply")
    assert (segment["state"], segment["current"]) == ("carried", "2")
    assert _branch(board)["tasks"][0]["status"] == "open"
    assert _steps(board) == [("1", "completed"), ("2", "in_progress")]
    assert shared.turn_owner("sess1", "", goal) == {"task": "T1", "step": "2"}
    # The model finishing it is what closes the task.
    with turn("m2_reply"):
        _task_step_impl(next=True)
    assert shared.close_at_turn_end("sess1", "m2_reply")["state"] == "done"
    assert _steps(board) == [("1", "completed"), ("2", "completed")]


def test_a_task_that_never_advanced_ends_without_ticking_its_steps(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
    segment = shared.close_at_turn_end("sess1", "m1_reply")
    # Closed, and not shown as 5/5.
    assert (segment["state"], segment["advanced"]) == ("done", False)
    assert [t["status"] for t in segment["todos"]] == ["in_progress"] + ["pending"] * 4
    assert _steps(board) == [("1", "in_progress")] + [(str(n), "pending") for n in range(2, 6)]
    assert _branch(board)["tasks"][0]["status"] == "done"


def test_a_goal_task_that_never_advanced_stays_open(board, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    monkeypatch.setattr(chat, "current_identity", lambda: {"goal_id": "g1", "revision": 2})
    with turn("m1_reply"):
        _task_start_impl("Migrate", steps=FIVE)
    task = _branch(board)["tasks"][0]
    assert (task["goal_id"], task["goal_revision"]) == ("g1", 2)
    assert _branch(board)["todos"][0]["goal_id"] == "g1"
    segment = shared.close_at_turn_end("sess1", "m1_reply")
    assert segment["state"] == "carried"
    assert _branch(board)["tasks"][0]["status"] == "open"
    assert _steps(board)[0] == ("1", "in_progress")


def test_a_reply_that_did_not_touch_the_task_does_not_close_it(board):
    with turn("m1_reply"):
        _task_start_impl("Fold", steps=FIVE)
        _task_step_impl(next=True)
    shared.close_at_turn_end("sess1", "m1_reply")
    before = board.read_text()
    assert shared.close_at_turn_end("sess1", "m2_reply") is None
    assert shared.close_at_turn_end("sess1", "m2_reply", scope="agent:worker") is None
    assert board.read_text() == before


# --- a task over several turns -----------------------------------------------


def test_a_later_turn_draws_its_own_segment_with_the_earlier_steps(board):
    with turn("m1_reply"):
        _task_start_impl("Migrate", steps=FIVE)
        _task_step_impl(next=True)
        _task_step_impl(next=True)
    shared.close_at_turn_end("sess1", "m1_reply")
    with turn("m2_reply"):
        out = _task_step_impl(next=True)
    # Step 3 was still in progress when the first reply ended; this call finishes it.
    assert _effect(out) == {"task": "T1", "step": "4"}
    first, second = _branch(board)["plans"]
    assert (first["anchor_id"], first["state"], first["earlier"]) == ("m1_reply", "carried", [])
    assert (second["anchor_id"], second["state"], second["earlier"], second["current"]) == (
        "m2_reply", "running", ["1", "2"], "4")
    # The earlier reply keeps the task as it left it.
    assert [t["status"] for t in first["todos"]] == ["completed"] * 2 + ["in_progress"] + ["pending"] * 2


def test_a_goal_turn_continues_the_open_task_from_its_first_block(board, monkeypatch, frames):
    from openprogram.programs.tools.planning.goal import chat
    goal = {"goal_id": "g1", "revision": 2}
    monkeypatch.setattr(chat, "current_identity", lambda: dict(goal))
    with turn("m1_reply"):
        _task_start_impl("Migrate", steps=FIVE)
        _task_step_impl(next=True)
    assert shared.turn_owner("sess1", "", goal) == {"task": "T1", "step": "2"}
    assert shared.turn_owner("sess1", "", {"goal_id": "g1", "revision": 3}) is None
    assert shared.turn_owner("sess1", "", None) is None
    assert shared.turn_owner("sess1", "agent:worker", goal) is None
    shared.close_at_turn_end("sess1", "m1_reply")
    # The step is still in progress between the turns: the next one goes on with it.
    assert shared.turn_owner("sess1", "", goal) == {"task": "T1", "step": "2"}
    frames.clear()
    shared.open_turn_segment("sess1", "m2_reply", "T1")
    (frame,) = frames
    (segment,) = frame["data"]["changed"]
    assert (segment["anchor_id"], segment["state"], segment["earlier"], segment["current"]) == (
        "m2_reply", "running", ["1"], "2")
    # Asked again, it is the same segment.
    shared.open_turn_segment("sess1", "m2_reply", "T1")
    assert len(_branch(board)["plans"]) == 2


# --- sub-agents ---------------------------------------------------------------


def test_a_sub_agents_task_is_its_own(board):
    with turn("m1_reply"):
        _task_start_impl("Parent work", steps=["a", "b"])
    with turn("job7_user_reply", source="agent_spawn", agent_id="reviewer"):
        _task_start_impl("Review", steps=["read"])
    stored = _branch(board)
    # Starting it did not close the parent's.
    # The scope names the run, not only the agent.
    own = "agent:reviewer:job7_user_reply"
    assert [(t["id"], t.get("scope"), t["status"]) for t in stored["tasks"]] == [
        ("T1", None, "open"), ("T2", own, "open")]
    assert stored["todos"][2]["scope"] == own
    assert [(p["anchor_id"], p["plan_id"], p["scope"]) for p in stored["plans"]] == [
        ("m1_reply", "T1", ""), ("job7_user_reply", "T2", own)]


def _spawn_scope(reply_id, agent_id="reviewer"):
    return shared.scope_of(SimpleNamespace(source="agent_spawn", agent_id=agent_id), reply_id)


def test_two_runs_of_one_agent_side_by_side_keep_their_own_tasks(board):
    with turn("job7_reply", source="agent_spawn", agent_id="reviewer"):
        _task_start_impl("Review A", steps=["read", "report"])
    with turn("job8_reply", source="agent_spawn", agent_id="reviewer"):
        _task_start_impl("Review B", steps=["read", "report"])
    # B starting did not displace A, and dropped none of its steps.
    assert [(t["title"], t["status"]) for t in _branch(board)["tasks"]] == [("Review A", "open"), ("Review B", "open")]
    with turn("job7_reply", source="agent_spawn", agent_id="reviewer"):
        out = _task_step_impl(next=True)
    # A's advance moved A's task.
    assert _effect(out) == {"task": "T1", "step": "2"}
    assert _steps(board) == [("1", "completed"), ("2", "in_progress"), ("3", "in_progress"), ("4", "pending")]
    assert shared.close_at_turn_end("sess1", "job7_reply", scope=_spawn_scope("job7_reply"))["plan_id"] == "T1"
    assert [(t["title"], t["status"]) for t in _branch(board)["tasks"]] == [("Review A", "done"), ("Review B", "open")]


def test_a_later_run_of_the_same_agent_does_not_inherit_the_earlier_runs_task(board):
    with turn("m1_reply"):
        _task_start_impl("Parent work", steps=["a", "b", "c"])
    with turn("job7_reply", source="agent_spawn", agent_id="reviewer"):
        _task_start_impl("Review", steps=["read", "compare", "report"])
        _task_step_impl(next=True)
    # The run ended with steps waiting: it has no next turn, so the task is
    # closed where it stood and no step is ticked for it.
    segment = shared.close_at_turn_end("sess1", "job7_reply", scope=_spawn_scope("job7_reply"))
    assert segment["state"] == "displaced"
    assert [t["status"] for t in segment["todos"]] == ["completed", "in_progress", "pending"]
    stored = _branch(board)
    assert stored["tasks"][1]["status"] == "displaced"
    assert _steps(board)[3:] == [("4", "completed"), ("5", "dropped"), ("6", "dropped")]
    # A second run with no task of its own ticks the parent's step, not the first run's.
    with turn("job9_reply", source="agent_spawn", agent_id="reviewer"):
        out = _task_step_impl(step=2, result="looks right")
    assert out.startswith("Step 2 of 'Parent work' marked done: b")
    assert _steps(board)[:3] == [("1", "in_progress"), ("2", "completed"), ("3", "pending")]


def test_a_stopped_run_leaves_nothing_a_later_run_can_pick_up(board):
    with turn("job7_reply", source="agent_spawn", agent_id="reviewer"):
        _task_start_impl("Review", steps=["read", "report"])
        _task_step_impl(next=True)
    # Stopped: the end of the turn closed nothing, the task is still open.
    with turn("job9_reply", source="agent_spawn", agent_id="reviewer"):
        assert _task_step_impl(next=True).startswith("No task of your own is open")
        assert shared.open_task(shared.load_tasks("sess1"), shared.current_scope()) is None
    assert _steps(board) == [("1", "completed"), ("2", "in_progress")]


def test_a_sub_agent_without_a_task_ticks_its_parents_step(board, frames):
    with turn("m1_reply"):
        _task_start_impl("Parent work", steps=["a", "b", "c"])
    frames.clear()
    with turn("job7_user_reply", source="agent_spawn", agent_id="reviewer"):
        assert "nothing was changed" in _task_step_impl(next=True)
        assert "nothing was changed" in _task_step_impl(step=2, outcome="all good")
        out = _task_step_impl(step=2, result="looks right")
    assert isinstance(out, str) and out.startswith("Step 2 of 'Parent work' marked done: b")
    stored = _branch(board)
    assert _steps(board) == [("1", "in_progress"), ("2", "completed"), ("3", "pending")]
    assert stored["todos"][1]["result"] == "looks right"
    # The parent's segment changed; the sub-agent's reply got none.
    assert _segments(board) == [
        ("m1_reply", "T1", "running", [("1", "in_progress"), ("2", "completed"), ("3", "pending")])]
    assert [f["data"]["changed"][0]["anchor_id"] for f in frames] == ["m1_reply"]


def test_a_sub_agent_with_nothing_to_tick_gets_a_hint(board):
    with turn("job7_user_reply", source="agent_spawn", agent_id="reviewer"):
        assert _task_step_impl(step=1).startswith("No task is open")


# --- frames, listing, older boards ---------------------------------------------


def test_each_change_sends_the_segments_that_changed(board, frames):
    with turn("m1_reply"):
        _task_start_impl("First", steps=["a", "b", "c"])
        _task_step_impl(next=True)
        _task_start_impl("Second")
    assert [f["type"] for f in frames] == ["todos_update"] * 3
    assert all(set(f["data"]) == {"session_id", "branch_id", "changed"} for f in frames)
    assert frames[0]["data"]["session_id"] == "sess1" and frames[0]["data"]["branch_id"] == "main"
    assert [[(s["plan_id"], s["state"]) for s in f["data"]["changed"]] for f in frames] == [
        [("T1", "running")], [("T1", "running")], [("T1", "displaced"), ("T2", "running")]]


def test_publish_without_a_server_is_quiet(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "openprogram.interfaces.server.server", None)
    shared.publish("sess1", [{"anchor_id": "a", "plan_id": "T1"}], "main")
    shared.publish("sess1", [None])


def test_a_change_outside_a_turn_places_no_segment(board):
    _task_start_impl("Fold", steps=["a"])
    assert _branch(board)["plans"] == [] and _branch(board)["tasks"][0]["status"] == "open"


def test_a_function_subprocess_anchor_names_the_reply(board):
    with turn("u1_reply|node:abc"):
        _task_start_impl("Fold")
    assert _segments(board)[0][0] == "u1_reply"


def test_list_groups_steps_by_task(board):
    assert _task_list_impl() == "(no tasks in this conversation)"
    with turn("m1_reply"):
        _task_start_impl("First", steps=["a", "b"])
        _task_step_impl(next=True, result="found it")
        _task_step_impl(outcome="shipped")
        _task_start_impl("Second", "Because.", ["c", "d", "e"])
        _task_step_impl(drop=[3])
    assert _task_list_impl() == (
        "T2 Second [open] 0/2\n"
        "  Because.\n"
        "  1. [in_progress] c\n"
        "  2. [pending] d\n"
        "  3. [dropped] e\n"
        "T1 First [done] 2/2 — shipped"
    )


def test_a_board_of_plain_todo_lists_is_read_as_stored(board):
    old_plan = {"anchor_id": "u1_reply", "plan_id": "1", "scope": "", "created_at": 1.0, "updated_at": 2.0,
                "todos": [{"id": "1", "subject": "old", "status": "completed"}]}
    old_todo = {"id": "1", "subject": "old", "description": "", "status": "completed", "plan": "1",
                "blocked_by": [], "created_at": 1.0, "updated_at": 2.0}
    board.write_text(json.dumps({"version": 3, "branches": {"main": {"todos": [old_todo], "plans": [old_plan]}}}))
    before = board.read_text()
    assert shared.load_all_plans("sess1") == [old_plan]
    assert shared.load_tasks("sess1") == []
    assert "Earlier todos:\n  [completed] old" in _task_list_impl()
    # Reading rewrites nothing.
    assert board.read_text() == before
    with turn("u2_reply"):
        _task_start_impl("New work", steps=["a"])
    stored = _branch(board)
    assert stored["plans"][0] == old_plan and stored["todos"][0] == old_todo
    assert stored["todos"][1]["id"] == "2" and stored["tasks"][0]["id"] == "T1"


def test_a_version_one_board_belongs_to_the_selected_branch(board):
    board.write_text(json.dumps({"version": 1, "todos": [{
        "id": "1", "subject": "old", "description": "", "status": "pending", "owner": "",
        "blocked_by": [], "created_at": 1.0, "updated_at": 1.0,
    }]}))
    assert shared.load_plans("sess1") == []
    with turn("u9_reply"):
        _task_start_impl("New work")
    assert list(json.loads(board.read_text())["branches"]) == ["main"]
    assert _branch(board)["todos"][0]["subject"] == "old"


def test_each_branch_keeps_its_own_board(board, monkeypatch):
    with turn("u1_reply"):
        _task_start_impl("Main work", steps=["a", "b"])
    # A branch made from main starts with a copy of main's board.
    monkeypatch.setattr(shared, "_parent_branch", lambda sid, bid: "main" if bid == "side" else None)
    monkeypatch.setattr(shared, "current_branch_id", lambda sid: "side")
    with turn("u2_reply"):
        _task_step_impl(next=True)
    assert _steps(board, "side") == [("1", "completed"), ("2", "in_progress")]
    assert _steps(board, "main") == [("1", "in_progress"), ("2", "pending")]
    assert _branch(board, "main")["tasks"][0]["advanced"] is False
    # The chat draws every branch's segments, each under its own reply.
    assert {p["anchor_id"] for p in shared.load_all_plans("sess1")} == {"u1_reply", "u2_reply"}


def test_saving_the_board_alone_keeps_tasks_and_segments(board):
    with turn("u1_reply"):
        _task_start_impl("Fold", steps=["a"])
    shared.save("sess1", shared.load("sess1"))
    assert len(shared.load_plans("sess1")) == 1 and len(shared.load_tasks("sess1")) == 1


def test_segments_are_capped(board, monkeypatch):
    monkeypatch.setattr(shared, "MAX_PLANS", 3)
    for n in range(6):
        with turn(f"u{n}_reply"):
            _task_start_impl(f"Task {n}")
    assert [p[0] for p in _segments(board)] == ["u3_reply", "u4_reply", "u5_reply"]


def test_session_load_sends_the_segments(board):
    from openprogram.interfaces.server.ws_actions.session import load
    with turn("u1_reply"):
        _task_start_impl("Fold", steps=["a"])
    (segment,) = load._session_todo_plans("sess1")
    assert (segment["anchor_id"], segment["title"], segment["state"]) == ("u1_reply", "Fold", "running")


def test_the_task_tools_are_offered_and_loaded_on_demand():
    from openprogram.programs import DEFAULT_TOOLS, RESIDENT_TOOLS
    from openprogram.programs.tools.planning import todo
    assert {"task_start", "task_step"} <= set(DEFAULT_TOOLS)
    # A step moves on with a real call's task_progress; task_step is for the rest.
    assert "task_start" in RESIDENT_TOOLS and not {"task_step", "task_list"} & RESIDENT_TOOLS
    assert not {"task_list", "todo_create", "todo_update", "todo_list"} & set(DEFAULT_TOOLS)
    assert [tool.name for tool in (todo.task_start, todo.task_step, todo.task_list)] == [
        "task_start", "task_step", "task_list"]
    assert set(todo.task_step.parameters["properties"]) == {"next", "step", "add", "drop", "result", "outcome"}
    assert todo.task_start.parameters["required"] == ["title"]
    for rule in ("three or more steps", "task_progress", '{"task"', '{"step": N}'):
        assert rule in todo.task_start.description
    assert "task_progress" in todo.task_step.description and "outcome" in todo.task_step.description
    # Said in the model's own terms: a round trip, not "a turn".
    for tool in (todo.task_start, todo.task_step):
        assert "round trip" in tool.description and "turn on this tool" not in tool.description
