"""Branches run side by side, each with its own settings, board and baselines.

Covers the pieces that decide which branch something belongs to: the
"follow the Agent" migration planner, where an entry continues, which
branches an operation holds, what a run on another branch waits for, which
commands stay with the selected branch, the per-branch todo board, a Goal
recorded before Goals had a branch, a fork's parent record, and read
baselines taken from the branch's own history.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.state.store.session import branch_config, operations
from openprogram.state.store.session.branch_config import (
    advance_branch,
    follow_agent_resets,
    last_written_branch,
)


# ---------------------------------------------------------------- B1 migration

def _agents(agent_id):
    return {"main": {"model": "anthropic/opus", "web_search": True},
            "night": {"model": "openai/gpt", "web_search": False}}.get(agent_id, {})


def test_a_model_copied_from_the_agent_goes_back_to_following_it_and_a_pick_stays():
    meta = {"agent_id": "main", "branch_refs": {
        "b1": {"config": {"agent_id": "main", "provider_override": "anthropic", "model_override": "opus",
                          "web_search": True}},
        "b2": {"config": {"agent_id": "night", "provider_override": "anthropic", "model_override": "opus",
                          "web_search": True}},
        "b3": {"config": {"agent_id": "night", "web_search": False}},
    }}
    resets = follow_agent_resets(meta, _agents)
    assert resets == {"b1": ["provider_override", "model_override", "web_search"],
                      "b3": ["web_search"]}
    # b2's model is the person's own pick (not its Agent's) and stays, as
    # does its web search switch that differs from the Agent's choice.
    for key, fields in resets.items():
        for name in fields:
            meta["branch_refs"][key]["config"][name] = None
    assert follow_agent_resets(meta, _agents) == {}


def test_a_conversation_without_branch_records_is_planned_on_its_own_fields():
    meta = {"agent_id": "main", "provider_override": "anthropic", "model_override": "opus"}
    assert follow_agent_resets(meta, _agents) == {None: ["provider_override", "model_override"]}


# ---------------------------------------------------------------- B5 entry

def test_an_entry_continues_on_the_branch_written_last_unless_it_was_dropped():
    meta = {"active_branch_id": "a", "last_branch_id": "b",
            "branch_refs": {"a": {}, "b": {"status": "active"}}}
    assert last_written_branch(meta) == "b"
    meta["branch_refs"]["b"]["status"] = "aborted"
    assert last_written_branch(meta) == "a"
    assert last_written_branch({"active_branch_id": "a", "last_branch_id": "gone"}) == "a"


# ---------------------------------------------------------------- B7 fork record

def test_a_new_branch_copies_the_settings_it_leaves_from_and_names_its_parent():
    nodes = {"u1": SimpleNamespace(predecessor=None), "a1": SimpleNamespace(predecessor="u1"),
             "u2": SimpleNamespace(predecessor="a1"), "u2b": SimpleNamespace(predecessor="a1")}
    meta = {"head_id": "u2", "active_branch_id": "main_b", "thinking_effort": "high",
            "branch_refs": {"main_b": {"branch_id": "main_b", "head_id": "u2",
                                       "config": {"thinking_effort": "high"}}}}
    advance_branch(meta, "s", "u2b", nodes)
    fork = meta["active_branch_id"]
    assert fork != "main_b"
    assert meta["branch_refs"][fork]["parent_branch_id"] == "main_b"
    assert meta["branch_refs"][fork]["config"]["thinking_effort"] == "high"
    # Later changes stay apart.
    meta["branch_refs"][fork]["config"]["thinking_effort"] = "low"
    assert meta["branch_refs"]["main_b"]["config"]["thinking_effort"] == "high"


# ---------------------------------------------------------------- B6 holds

class _DB:
    def __init__(self, row, chains):
        self.row, self.chains = row, chains

    def get_session(self, _sid):
        return self.row

    def get_branch(self, _sid, head):
        return [{"id": node} for node in self.chains.get(head, [])]


@pytest.fixture
def lifecycle(monkeypatch):
    import openprogram.interfaces.server.server  # noqa: F401 - loads the runtime in order
    from openprogram.interfaces.server.server_runtime import session_lifecycle
    row = {"active_branch_id": "m", "head_id": "m3",
           "branch_refs": {"m": {"head_id": "m3"}, "t": {"head_id": "t2"},
                           "x": {"head_id": "x1", "status": "aborted"}}}
    chains = {"m3": ["r", "m2", "m3"], "t2": ["r", "t1", "t2"], "x1": ["r", "x1"]}
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: _DB(row, chains))
    return session_lifecycle


def test_a_node_on_a_common_ancestor_belongs_to_every_branch_below_it(lifecycle):
    assert lifecycle.branches_through("s", ["r"]) == ["m", "t"]
    assert lifecycle.branches_through("s", ["t1"]) == ["t"]
    assert lifecycle.branches_through("s", ["m3"]) == ["m"]
    assert lifecycle.branches_through("s", []) == []


def test_an_operation_holds_only_its_branches_in_order_and_gives_back_on_refusal(lifecycle, monkeypatch):
    taken, released = [], []
    busy = {"t"}

    def reserve(_sid, _kind, owner, *, branch_id=None, key=None):
        if (branch_id or "m") in busy:
            return {"ok": False, "code": "busy"}
        taken.append((owner, branch_id))
        return None

    monkeypatch.setattr(lifecycle, "_selected_branch", lambda _sid: "m")
    monkeypatch.setattr(lifecycle, "reserve_operation", reserve)
    monkeypatch.setattr(lifecycle, "_release_run_reservation", lambda _sid, owner: released.append(owner))
    monkeypatch.setattr(lifecycle, "running_branch_among",
                        lambda _sid, ids: {"branch_id": ids[0], "branch_name": "Night", "op_kind": "reply"})
    refusal, owners = lifecycle.reserve_branches_operation("s", ["t", "m"], "delete", "op1")
    assert owners == [] and refusal["blocking"]["branch_id"] == "t"
    assert "Night" in refusal["error"]
    # "m" sorts first and was held, so it is given back.
    assert taken == [("op1@m", None)] and released == ["op1@m"]
    busy.clear()
    taken.clear()
    refusal, owners = lifecycle.reserve_branches_operation("s", ["t", "m"], "delete", "op2")
    assert refusal is None and owners == ["op2@m", "op2@t"]
    assert taken == [("op2@m", None), ("op2@t", "t")]


# ---------------------------------------------------------------- step 7 hold

def test_a_scheduled_run_sees_a_branch_held_by_any_process(monkeypatch):
    ops = [{"op_id": "1", "branch_id": "t", "scope": "branch"}]
    monkeypatch.setattr(operations, "running_operations", lambda _sid, root=None: list(ops))
    assert operations.branch_operation_held("s", "t")["op_id"] == "1"
    assert operations.branch_operation_held("s", "u") is None
    # A task with no branch yet is never held by a branch's operation.
    assert operations.branch_operation_held("s", None) is None
    ops[:] = [{"op_id": "2", "branch_id": None, "scope": "branch"}]
    assert operations.branch_operation_held("s", "m", selected="m")["op_id"] == "2"
    assert operations.branch_operation_held("s", "t", selected="m") is None
    ops[:] = [{"op_id": "3", "branch_id": "m", "scope": "conversation"}]
    assert operations.branch_operation_held("s", None)["op_id"] == "3"


# ---------------------------------------------------------------- B9 wait state

def test_a_run_on_another_branch_says_what_it_waits_for(monkeypatch):
    from openprogram.interfaces.server.server_runtime import session_persistence as persistence
    assert persistence.branch_wait_state({"reason_code": None}) is None
    assert persistence.branch_wait_state({"reason_code": "system_access_required"}) == "approval"
    import openprogram.runtime.execution.waits as waits
    kinds = {"e1": "ask", "e2": "approval"}

    class Store:
        def __init__(self, _store):
            pass

        def list_open(self, *, execution_id):
            return [SimpleNamespace(kind=kinds[execution_id])]

    monkeypatch.setattr(waits, "DurableWaitStore", Store)
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: None)
    assert persistence.branch_wait_state({"reason_code": "wait_open", "execution_id": "e1"}) == "input"
    assert persistence.branch_wait_state({"reason_code": "wait_open", "execution_id": "e2"}) == "approval"


# ---------------------------------------------------------------- B8 commands

@pytest.mark.parametrize("text, wide", [
    ("/clear", True), ("/rename New name", True), ("/goal ship the release", True),
    ("/goal", False), ("/goal status", False), ("/goal pause", False),
    ("/compact", False), ("/model x", False), ("/review", False), ("hello /clear", False),
])
def test_only_commands_that_change_the_whole_conversation_stay_with_the_selected_branch(text, wide):
    from openprogram.interfaces.server.ws_actions.chat.turn import conversation_wide_command
    assert conversation_wide_command(text) is wide


# ---------------------------------------------------------------- B8 boards

class _Doc:
    def __init__(self):
        self.text = None

    def exists(self):
        return self.text is not None

    def read_text(self, encoding="utf-8"):
        if self.text is None:
            raise FileNotFoundError
        return self.text

    def write_text(self, content, encoding="utf-8"):
        self.text = content


@pytest.fixture
def board(monkeypatch):
    from openprogram.programs.tools.planning.todo import shared
    doc = _Doc()
    parents = {"fork": "main", "fork2": "fork"}
    monkeypatch.setattr(shared, "todos_path", lambda _sid: doc)
    monkeypatch.setattr(shared, "_selected_branch", lambda _sid: "main")
    monkeypatch.setattr(shared, "_parent_branch", lambda _sid, branch: parents.get(branch))
    monkeypatch.setattr(shared, "current_branch_id", lambda _sid: "main")
    monkeypatch.setattr(shared, "publish", lambda *a, **k: None, raising=False)
    return shared, doc


def test_each_branch_keeps_its_own_board_and_a_fork_starts_from_a_copy(board):
    shared, doc = board
    shared.save("s", [{"id": "1", "content": "main task"}], branch_id="main")
    assert shared.load("s", "fork") == [{"id": "1", "content": "main task"}]
    # The copy is stored on the fork at once, so changes stay apart.
    assert "fork" in json.loads(doc.text)["branches"]
    shared.save("s", [{"id": "1", "content": "fork task"}], branch_id="fork")
    assert shared.load("s", "main") == [{"id": "1", "content": "main task"}]
    assert shared.load("s", "fork2") == [{"id": "1", "content": "fork task"}]
    assert shared.load("s", "other") == []
    assert json.loads(doc.text)["version"] == 3


def test_an_older_single_board_is_the_selected_branchs(board):
    shared, doc = board
    doc.text = json.dumps({"todos": [{"id": "1", "content": "old"}],
                           "plans": [{"anchor_id": "a", "plan_id": "p", "todos": []}]})
    assert shared.load("s", "main") == [{"id": "1", "content": "old"}]
    assert shared.load_all_plans("s") == [{"anchor_id": "a", "plan_id": "p", "todos": []}]


# ---------------------------------------------------------------- B8 Goal

def test_a_goal_saved_before_goals_had_a_branch_belongs_to_the_selected_one(monkeypatch):
    from openprogram.programs.tools.planning.goal import state as goal_state
    goal = {"objective": "ship it", "status": "active"}
    db = SimpleNamespace(read_disk_meta=lambda _sid: {"goal": dict(goal), "active_branch_id": "main"})
    monkeypatch.setattr(goal_state._goal, "_db", lambda: db)
    assert goal_state.load_goal("s")["branch_id"] == "main"
    goal["branch_id"] = "fork"
    assert goal_state.load_goal("s")["branch_id"] == "fork"


# ---------------------------------------------------------------- headless fork

def test_a_retry_or_edit_starts_a_sibling_branch_and_a_retry_without_reply_reruns():
    from openprogram.runtime.conversation.branch_fork import ForkError, plan_fork
    messages = [{"id": "u1", "role": "user", "content": "hi", "predecessor": None},
                {"id": "a1", "role": "assistant", "predecessor": "u1"},
                {"id": "u2", "role": "user", "content": "again", "predecessor": "a1"}]
    retry = plan_fork(messages, "a1", None)
    assert retry.new_branch and retry.message["predecessor"] == "ROOT"
    assert retry.message["content"] == "hi" and retry.message["forked_from"] == "u1"
    edit = plan_fork(messages, "u1", "hello", authority={"origin": "cli"})
    assert edit.message["edit_of"] == "u1" and edit.message["content"] == "hello"
    rerun = plan_fork(messages, "u2", None)
    assert not rerun.new_branch and rerun.msg_id == "u2"
    with pytest.raises(ForkError) as missing:
        plan_fork(messages, "nope", None)
    assert missing.value.status == 404


# ---------------------------------------------------------------- step 6 baselines

def test_a_read_baseline_comes_from_the_branchs_own_history(monkeypatch, tmp_path):
    from openprogram.state.store.snapshot import read_tracking as rt
    doc = _Doc()
    anchor = {"node": "a1"}
    lines = {"a1": ({"u1", "a1"}, 0.0), "b1": ({"u1", "b1"}, 0.0)}
    monkeypatch.setattr(rt, "_current_anchor", lambda: anchor["node"])
    monkeypatch.setattr(rt, "_ledger", lambda _sid: doc)
    monkeypatch.setattr(rt, "_line_of", lambda _sid, node: lines.get(node))
    target = tmp_path / "f.txt"
    target.write_text("one")
    path = str(target)
    assert rt.check_fresh(path, session_id="s") == rt.NEVER_READ
    assert rt.mark_seen(path, session_id="s")["node"] == "a1"
    assert rt.check_fresh(path, session_id="s") == rt.FRESH
    # Another process (no in-memory record) on the same line agrees.
    rt.forget_session("s")
    assert rt.check_fresh(path, session_id="s") == rt.FRESH
    # A sibling branch never read it.
    anchor["node"] = "b1"
    assert rt.check_fresh(path, session_id="s") == rt.NEVER_READ
    # A compaction after the read on the same line makes the model read again.
    anchor["node"] = "a1"
    lines["a1"] = ({"u1", "a1"}, 10**12)
    assert rt.check_fresh(path, session_id="s") == rt.NEVER_READ
    lines["a1"] = ({"u1", "a1"}, 0.0)
    target.write_text("two")
    assert rt.check_fresh(path, session_id="s") == rt.STALE


# ---------------------------------------------------------------- B1 drift

def test_a_turn_runs_on_its_agents_model_as_it_is_now_and_finds_the_last_turns(monkeypatch):
    from openprogram.runtime.conversation import branch_config as freeze
    from openprogram.runtime.conversation import session_config
    monkeypatch.setattr(session_config, "_agent_settings", lambda agent: {"model": f"p/{agent}"})
    monkeypatch.setattr(session_config, "_global_settings", lambda _name: "p/global")
    assert freeze._effective_model({"model_override": "x/y"}, {"model_override": "request"}) == ("x/y", "branch")
    assert freeze._effective_model({"agent_id": "main"}, {}) == ("p/main", "agent")
    snap = {"profile_snapshot": {"model": {"provider": "t", "id": "trial"}}}
    assert freeze._effective_model(snap, {}) == ("t/trial", "agent")
    chain = [{"id": "u1", "role": "user", "turn_settings": {"model": "p/old"}},
             {"id": "a1", "role": "assistant"},
             {"id": "u2", "role": "user", "metadata": {"turn_settings": {"model": "p/new"}}},
             {"id": "a2", "role": "assistant"}]
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db",
                        lambda: SimpleNamespace(get_branch=lambda _sid, _head: chain))
    assert freeze._previous_turn_model("s", "a2") == "p/new"
    assert freeze._previous_turn_model("s", "ROOT") is None
