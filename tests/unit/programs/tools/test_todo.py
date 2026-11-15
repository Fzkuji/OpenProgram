"""Unit tests for the todo planning board tool family.

All calls go through the _impl functions (the @function binding object
is not callable) with storage redirected to a tmp path. No LLM requests.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from openprogram.programs.tools.planning.todo import shared
from openprogram.programs.tools.planning.todo.todo_create.todo_create import _todo_create_impl
from openprogram.programs.tools.planning.todo.todo_list.todo_list import _todo_list_impl
from openprogram.programs.tools.planning.todo.todo_update.todo_update import _todo_update_impl


@pytest.fixture
def board(monkeypatch, tmp_path):
    """Session context + todos.json redirected to a tmp file."""
    monkeypatch.setattr(shared, "current_session_id", lambda: "sess1")
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")
    # The selected branch, without a session database behind it.
    monkeypatch.setattr(shared, "_selected_branch", lambda sid: "main")
    monkeypatch.setattr(shared, "_parent_branch", lambda sid, bid: None)
    return tmp_path / "todos.json"


def _branch(board, branch_id="main"):
    """One branch's ``{todos, plans}`` as stored in todos.json (version 3)."""
    blob = json.loads(board.read_text())
    assert blob["version"] == 3
    return blob["branches"][branch_id]


def test_create_returns_id_and_persists(board):
    out = _todo_create_impl("write tests", "cover the board")
    assert out == "Todo #1 created: write tests"
    (entry,) = _branch(board)["todos"]
    assert entry["subject"] == "write tests"
    assert entry["description"] == "cover the board"
    assert entry["status"] == "pending"
    assert entry["blocked_by"] == []
    assert entry["created_at"] == entry["updated_at"]


def test_create_ids_increment(board):
    _todo_create_impl("a")
    out = _todo_create_impl("b")
    assert "#2" in out


def test_create_requires_subject(board):
    assert "subject required" in _todo_create_impl("  ")


def test_create_rejects_unknown_blocked_by(board):
    _todo_create_impl("a")
    out = _todo_create_impl("b", blocked_by="1, 9")
    assert "unknown todo id(s): 9" in out


def test_update_fields_and_status(board):
    _todo_create_impl("a")
    out = _todo_update_impl("1", status="in_progress")
    assert out == "Todo #1 updated (status)"
    (entry,) = _branch(board)["todos"]
    assert entry["status"] == "in_progress"
    # Who works on what is the plan's job now; todos carry no owner.
    assert "owner" not in entry


def test_update_rejects_bad_status(board):
    _todo_create_impl("a")
    assert "invalid status" in _todo_update_impl("1", status="done")


def test_update_unknown_id(board):
    assert "unknown todo_id" in _todo_update_impl("42")


def test_update_no_changes(board):
    _todo_create_impl("a")
    assert "unchanged" in _todo_update_impl("1")


def test_list_empty(board):
    assert _todo_list_impl() == "(no todos in this session)"


def test_list_groups_and_annotations(board):
    _todo_create_impl("first")
    _todo_create_impl("second", blocked_by="1")
    _todo_create_impl("third")
    _todo_update_impl("1", status="completed")
    _todo_update_impl("3", status="in_progress")
    # A board written before owners were dropped still shows its owner.
    blob = json.loads(board.read_text())
    blob["branches"]["main"]["todos"][2]["owner"] = "claude"
    board.write_text(json.dumps(blob))
    out = _todo_list_impl()
    lines = out.splitlines()
    assert lines[0] == "in_progress:"
    assert lines[1] == "- #3 third  (owner: claude)"
    assert lines[2] == "pending:"
    assert lines[3] == "- #2 second  (blocked by #1)"
    assert lines[4] == "completed:"
    assert lines[5] == "- #1 first"


def test_no_session_context(monkeypatch):
    monkeypatch.setattr(shared, "current_session_id", lambda: None)
    assert "no active session context" in _todo_create_impl("a")
    assert "no active session context" in _todo_update_impl("1")
    assert "no active session context" in _todo_list_impl()


def test_saved_board_is_pushed_to_the_ui(board, monkeypatch):
    frames = []
    monkeypatch.setattr(shared, "publish", lambda sid, todos, plans=None: frames.append((sid, [dict(t) for t in todos])))
    _todo_create_impl("a")
    _todo_update_impl("1", status="in_progress")
    _todo_update_impl("1")  # no change → nothing to push
    assert [(sid, [(t["id"], t["status"]) for t in todos]) for sid, todos in frames] == [
        ("sess1", [("1", "pending")]),
        ("sess1", [("1", "in_progress")]),
    ]


def test_publish_without_a_server_is_quiet(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "openprogram.interfaces.server.server", None)
    shared.publish("sess1", [{"id": "1"}])


# --- Plans placed under the turn that changed them -------------------------


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


def _placed(board, branch_id="main"):
    plans = _branch(board, branch_id)["plans"]
    return [(p["anchor_id"], p["plan_id"], [(t["id"], t["status"]) for t in p["todos"]]) for p in plans]


def test_a_plan_is_placed_under_the_turn_that_made_it(board):
    with turn("u1_reply"):
        _todo_create_impl("a")
        _todo_create_impl("b")
        _todo_update_impl("1", status="in_progress")
    assert _placed(board) == [("u1_reply", "1", [("1", "in_progress"), ("2", "pending")])]


def test_a_later_turn_gets_its_own_copy_and_the_earlier_one_keeps_its_state(board):
    with turn("u1_reply"):
        _todo_create_impl("a")
        _todo_create_impl("b")
    with turn("u2_reply"):
        _todo_update_impl("1", status="completed")
    assert _placed(board) == [
        ("u1_reply", "1", [("1", "pending"), ("2", "pending")]),
        ("u2_reply", "1", [("1", "completed"), ("2", "pending")]),
    ]


def test_a_new_plan_after_a_finished_one_is_a_second_entry_in_the_same_turn(board, monkeypatch):
    clock = iter(range(100, 200))
    # The tools and the board share the one ``time`` module.
    monkeypatch.setattr(shared.time, "time", lambda: float(next(clock)))
    with turn("u1_reply"):
        _todo_create_impl("a")
        _todo_update_impl("1", status="completed")
        _todo_create_impl("b")
    assert _placed(board) == [
        ("u1_reply", "1", [("1", "completed")]),
        ("u1_reply", "2", [("2", "pending")]),
    ]


def test_a_change_outside_a_turn_places_nothing(board):
    _todo_create_impl("a")
    assert _placed(board) == []


def test_a_function_subprocess_anchor_names_the_reply(board):
    with turn("u1_reply|node:abc"):
        _todo_create_impl("a")
    assert _placed(board)[0][0] == "u1_reply"


def test_a_sub_agent_plan_is_its_own(board):
    with turn("u1_reply"):
        _todo_create_impl("parent step")
    with turn("job7_user_reply", source="agent_spawn", agent_id="worker"):
        _todo_create_impl("worker step")
    todos = _branch(board)["todos"]
    assert "scope" not in todos[0]
    assert todos[1]["scope"] == "agent:worker"
    assert _placed(board) == [
        ("u1_reply", "1", [("1", "pending")]),
        ("job7_user_reply", "2", [("2", "pending")]),
    ]


def test_a_sub_agent_ticking_a_parent_todo_updates_the_parent_card(board):
    with turn("u1_reply"):
        _todo_create_impl("parent step")
    with turn("job7_user_reply", source="agent_spawn", agent_id="worker"):
        _todo_update_impl("1", status="completed")
    assert _placed(board) == [("u1_reply", "1", [("1", "completed")])]


def test_a_version_one_board_still_reads_and_upgrades(board):
    board.write_text(json.dumps({"version": 1, "todos": [{
        "id": "1", "subject": "old", "description": "", "status": "pending", "owner": "",
        "blocked_by": [], "created_at": 1.0, "updated_at": 1.0,
    }]}))
    assert shared.load_plans("sess1") == []
    assert "#1 old" in _todo_list_impl()
    with turn("u9_reply"):
        _todo_update_impl("1", status="completed")
    # The older file's single board now belongs to the selected branch.
    assert list(json.loads(board.read_text())["branches"]) == ["main"]
    assert _branch(board)["todos"][0]["status"] == "completed"
    assert _placed(board) == [("u9_reply", "1", [("1", "completed")])]


def test_each_branch_keeps_its_own_board(board, monkeypatch):
    with turn("u1_reply"):
        _todo_create_impl("main step")
    # A branch made from main starts with a copy of main's board.
    monkeypatch.setattr(shared, "_parent_branch", lambda sid, bid: "main" if bid == "side" else None)
    monkeypatch.setattr(shared, "current_branch_id", lambda sid: "side")
    with turn("u2_reply"):
        _todo_update_impl("1", status="completed")
        _todo_create_impl("side step")
    assert [(t["id"], t["status"]) for t in _branch(board, "side")["todos"]] == [("1", "completed"), ("2", "pending")]
    assert [(t["id"], t["status"]) for t in _branch(board, "main")["todos"]] == [("1", "pending")]
    # The chat draws every branch's placed plans, each under its own turn.
    assert {p["anchor_id"] for p in shared.load_all_plans("sess1")} == {"u1_reply", "u2_reply"}


def test_frames_carry_the_placed_plans(board, monkeypatch):
    frames = []
    monkeypatch.setattr(shared, "publish", lambda sid, todos, plans=None: frames.append(plans))
    with turn("u1_reply"):
        _todo_create_impl("a")
    assert frames[-1][0]["anchor_id"] == "u1_reply"
    assert frames[-1][0]["todos"] == [{"id": "1", "subject": "a", "status": "pending"}]


def test_saving_the_board_alone_keeps_the_placed_plans(board):
    with turn("u1_reply"):
        _todo_create_impl("a")
    shared.save("sess1", shared.load("sess1"))
    assert len(shared.load_plans("sess1")) == 1


def test_placed_plans_are_capped(board, monkeypatch):
    monkeypatch.setattr(shared, "MAX_PLANS", 3)
    with turn("u0_reply"):
        _todo_create_impl("a")
    for n in range(1, 6):
        with turn(f"u{n}_reply"):
            _todo_update_impl("1", subject=f"a{n}")
    assert [p[0] for p in _placed(board)] == ["u3_reply", "u4_reply", "u5_reply"]


def test_session_load_sends_the_placed_plans(board):
    from openprogram.interfaces.server.ws_actions.session import load
    with turn("u1_reply"):
        _todo_create_impl("a")
    assert load._session_todo_plans("sess1")[0]["anchor_id"] == "u1_reply"
