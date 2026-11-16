"""A branch's record follows the branch through a turn.

One branch; a turn is under way (its message written, its reply not yet)
when the person renames the branch on the tip they see and picks another
model for it. Whether that lands before the reply, while the reply's
append holds the conversation lock, after the reply, or after the turn
ended, the branch keeps one record at its new tip holding the name, the
lock, the turn count from before plus this turn, and its own settings.
Runs on the real store with a real thread for the write under the lock.
"""
from __future__ import annotations

import threading

import pytest

from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: s)
    import openprogram.state.store.session.session_store as ss_mod
    monkeypatch.setattr(ss_mod.shared, "_default_store", s)
    yield s
    s.close()


def _finalize(store, sid, reply):
    """What finalize_turn does to the branch record at the turn's end."""
    from openprogram.runtime.conversation.turn_runtime.titles import maybe_auto_name_branch
    store.carry_branch_entry(sid, reply)
    maybe_auto_name_branch(store, sid, reply)


def _write(store, sid, tip):
    """A person's writes to the branch as they saw it: a rename on the
    tip on screen and a model pick on the branch."""
    branch = store.get_session(sid)["active_branch_id"]
    store.set_branch_name(sid, tip, "Mid-turn", name_locked=True)
    store.update_session(sid, model_override="other-model", expected_branch_id=branch)


@pytest.mark.parametrize("when", ["before_reply", "during_reply", "after_reply", "after_finalize"])
def test_branch_record_follows_a_turn_whatever_is_written_in_between(store, monkeypatch, when):
    from openprogram.runtime.conversation import side_work
    from openprogram.state.store.session import branch_config

    monkeypatch.setattr(side_work, "allowed", lambda: False)
    sid = "s1"
    store.create_session(sid, "main", title="t")

    def add(mid, role, prev):
        store.append_message(sid, {"id": mid, "role": role, "content": mid,
                                   "predecessor": prev, "agent_id": "main"})

    add("u0", "user", "")
    add("a1", "assistant", "u0")
    _finalize(store, sid, "a1")
    branch = store.get_session(sid)["active_branch_id"]
    assert store.get_branch_meta(sid, "a1")["turns"] == 1

    add("u2", "user", "a1")
    seen = store.get_session(sid)["head_id"]  # the tip on screen mid-turn
    assert seen == "u2"
    if when == "before_reply":
        _write(store, sid, seen)
        add("a3", "assistant", "u2")
    elif when == "during_reply":
        # The writes reach the store while the reply's append holds the
        # conversation lock, so they land right after it, on the old tip.
        real = branch_config.advance_branch
        writer: list[threading.Thread] = []

        def advance(meta, *args, **kwargs):
            if not writer:
                thread = threading.Thread(target=_write, args=(store, sid, seen))
                writer.append(thread)
                thread.start()
                thread.join(0.2)
            return real(meta, *args, **kwargs)

        monkeypatch.setattr(branch_config, "advance_branch", advance)
        add("a3", "assistant", "u2")
        writer[0].join(10)
        assert not writer[0].is_alive()
        monkeypatch.setattr(branch_config, "advance_branch", real)
    else:
        add("a3", "assistant", "u2")
        if when == "after_reply":
            _write(store, sid, seen)
    _finalize(store, sid, "a3")
    if when == "after_finalize":
        _write(store, sid, seen)

    session = store.get_session(sid)
    assert session["active_branch_id"] == branch
    record = store.get_branch_meta(sid, "a3")
    assert record["name"] == "Mid-turn" and record["name_locked"] is True
    assert record["turns"] == 2
    assert set(store.branch_entries(sid)) == {"a3"}
    assert session["model_override"] == "other-model"
    assert session["branch_refs"][branch]["config"]["model_override"] == "other-model"
    rows = store.list_branches(sid)
    assert [(row["head_msg_id"], row.get("branch_id"), row["name"]) for row in rows] == [
        ("a3", branch, "Mid-turn")]


def _add(store, sid, mid, role, prev, **extra):
    store.append_message(sid, {"id": mid, "role": role, "content": mid,
                               "predecessor": prev, "agent_id": "main", **extra})


def _live_refs(store, sid):
    return {key: ref["head_id"] for key, ref in store.get_session(sid)["branch_refs"].items()
            if ref.get("status") != "aborted"}


def test_a_turn_that_ends_after_a_later_one_started_makes_no_branch(store):
    """A background-command notice is still being answered when the Goal's
    next turn is written under its reply. The notice then ends and records
    its reply as the head: the branch is already past it, so nothing moves."""
    sid = "late"
    store.create_session(sid, "main", title="t")
    _add(store, sid, "u0", "user", "")
    _add(store, sid, "a0", "assistant", "u0")
    _add(store, sid, "notice", "user", "a0")
    _add(store, sid, "notice_reply", "assistant", "notice")
    _add(store, sid, "goal", "user", "notice_reply")
    _add(store, sid, "goal_reply", "assistant", "goal")
    branch = store.get_session(sid)["active_branch_id"]

    store.update_session(sid, head_id="notice_reply", last_prompt_tokens=7,
                         head_forward_only=True, expected_branch_id=branch)

    session = store.get_session(sid)
    assert session["head_id"] == "goal_reply" and session["active_branch_id"] == branch
    assert session["last_prompt_tokens"] == 7
    assert _live_refs(store, sid) == {branch: "goal_reply"}
    # Going back on purpose still moves the head.
    store.set_head(sid, "notice_reply")
    assert store.get_session(sid)["head_id"] == "notice_reply"


def test_turns_on_a_branch_that_is_not_selected_continue_one_line(store):
    """A Goal keeps its branch while the person looks at another: every
    continuation and notice goes under the last one, the Goal's record
    follows, and neither the selection nor the number of branches changes."""
    from types import SimpleNamespace
    from openprogram.runtime.agent.turn_request import INHERIT_PARENT
    from openprogram.runtime.conversation.turn_runtime.prep import _own_branch_tip
    from openprogram.runtime.conversation.turn_runtime.turn_writer import TurnWriter
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    sid = "goal"
    store.create_session(sid, "main", title="t")
    _add(store, sid, "u0", "user", "")
    _add(store, sid, "a0", "assistant", "u0")
    _add(store, sid, "u1", "user", "a0")
    _add(store, sid, "a1", "assistant", "u1")
    goal_branch = store.get_session(sid)["active_branch_id"]
    # The person edits an earlier message: a branch of their own, selected.
    store.set_head(sid, "a0")
    _add(store, sid, "edit", "user", "a0")
    _add(store, sid, "edit_reply", "assistant", "edit")
    selected = store.get_session(sid)["active_branch_id"]
    assert selected != goal_branch
    before = _live_refs(store, sid)
    assert before[goal_branch] == "a1"

    tips = []
    for turn in range(3):
        req = SimpleNamespace(session_id=sid, branch_id=goal_branch, branch_from=INHERIT_PARENT,
                              advance_head=True, side_branch=False, spawn_caller=None,
                              user_text=f"continue {turn}", source="web")
        tip = _own_branch_tip(store.get_session(sid), req)
        tips.append(tip)
        writer = TurnWriter(store, req)
        user = f"goal{turn}_user"
        writer.persist_user(user, {"id": user, "role": "user", "content": req.user_text,
                                   "display": "runtime"}, tip)
        SessionNodeWriter(store, sid, advance_head=writer._moves_head()).append(
            Call(id=f"{user}_reply", role="assistant", output="ok", predecessor=user))

    assert tips == ["a1", "goal0_user_reply", "goal1_user_reply"]
    session = store.get_session(sid)
    assert session["active_branch_id"] == selected and session["head_id"] == "edit_reply"
    assert _live_refs(store, sid) == {**before, goal_branch: "goal2_user_reply"}


def test_goal_takes_the_branch_its_last_turn_was_written_on(store, monkeypatch):
    """A conversation saved while each runtime turn was recorded as its own
    branch: the Goal still names the first one, left behind. Its next turn
    continues where the last one ended, and its tasks go with it."""
    from types import SimpleNamespace
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    from openprogram.state.store.session.graph.branches import runtime_started_heads

    sid = "saved"
    store.create_session(sid, "main", title="t")
    _add(store, sid, "u0", "user", "")
    _add(store, sid, "a0", "assistant", "u0")
    _add(store, sid, "u1", "user", "a0")
    _add(store, sid, "a1", "assistant", "u1")
    goal_branch = store.get_session(sid)["active_branch_id"]
    # What the store used to record: a runtime turn as a branch of its own.
    store.set_head(sid, "a0")
    _add(store, sid, "r2", "user", "a0", display="runtime")
    _add(store, sid, "r2_reply", "assistant", "r2")
    latest = store.get_session(sid)["active_branch_id"]
    assert _live_refs(store, sid) == {goal_branch: "a1", latest: "r2_reply"}

    from openprogram.state.store import SessionNodeWriter
    nodes = SessionNodeWriter(store, sid).load().nodes
    # The runtime's branch is told apart from the one a person wrote on.
    assert runtime_started_heads(nodes, ["a1", "r2_reply"]) == {"r2_reply"}

    shared.save(sid, [{"id": "1", "content": "step", "status": "in_progress"}], [], [], goal_branch)
    shared.load(sid, latest)  # the selected branch took its own copy earlier
    shared.save(sid, [{"id": "1", "content": "step", "status": "completed"}], [], [], goal_branch)
    monkeypatch.setattr(chat, "publish", lambda _sid, goal: goal)
    executions = SimpleNamespace(get_execution_input=lambda _id: SimpleNamespace(assistant_message_id="r2_reply"))

    goal = chat.follow_last_turn(executions, sid, {"branch_id": goal_branch}, "exec")

    assert goal["branch_id"] == latest
    assert shared.load(sid, latest)[0]["status"] == "completed"
    # On its own line already: nothing changes.
    assert chat.follow_last_turn(executions, sid, {"branch_id": latest}, "exec")["branch_id"] == latest


def test_a_goal_line_a_person_forked_off_is_not_the_runtimes_branch():
    """A person forks Y off a reply on the Goal's line X, and the Goal's
    next turns go on under that reply on X. By shape alone X now looks like
    a line the runtime opened beside Y; the records say X is the Goal's and
    Y is selected, so neither is left out of the lists."""
    from types import SimpleNamespace
    from openprogram.state.store.session.graph.branches import _branches_in_use, runtime_started_heads

    def node(role, predecessor, display=None):
        return SimpleNamespace(role=role, predecessor=predecessor,
                               metadata={"display": display} if display else {})

    nodes = {"u1": node("user", None), "r1": node("assistant", "u1"),
             "u2": node("user", "r1", "user"),
             "g1": node("user", "r1", "runtime"), "rg1": node("assistant", "g1")}
    heads = ["u2", "rg1"]
    assert runtime_started_heads(nodes, heads) == {"rg1"}

    session = {"active_branch_id": "Y", "head_id": "u2", "extra_meta": {"goal": {"branch_id": "X"}}}
    leaves = [{"head_msg_id": "u2", "branch_id": "Y"}, {"head_msg_id": "rg1", "branch_id": "X"}]
    assert runtime_started_heads(nodes, heads, kept=_branches_in_use(session, leaves)) == set()
