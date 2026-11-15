"""_execute_agent_turn — same-session multi-agent execution primitive.

Spawn is just another branch (or a new root) in the parent session's
DAG. There is no separate sub_session id and no attach pointer
written by ``_execute_agent_turn`` itself — those are decisions for the
caller (e.g. ``_run_spawn`` in the webui broadcasts a result; merge
writes explicit multi-parent commits).

Tests:
  * inherit mode forks off a given node (same session) and stamps
    ``agent_id`` on the new turn's metadata.
  * clean mode writes a new root (``caller=null``) in the same
    session.
  * unknown session is reported as a structured failure.
  * dispatcher failure is surfaced (not swallowed).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def parent_store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store", lambda: s,
    )

    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "first turn",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "initial parent turn")
    return s


@pytest.fixture
def fake_dispatcher(monkeypatch):
    from openprogram.runtime.conversation import turn_runtime as disp

    class _R:
        def __init__(self, text, asst_id="sub_a", failed=False, error=None):
            self.final_text = text
            self.user_msg_id = "sub_u"
            self.assistant_msg_id = asst_id
            self.tool_calls = []
            self.usage = {}
            self.duration_ms = 1
            self.failed = failed
            self.error = error

    captured: dict = {"calls": []}

    def fake_run(req, *, on_event=None, cancel_event=None):
        captured["calls"].append({
            "session_id": req.session_id,
            "prompt": req.user_text,
            "history_override": req.history_override,
            "agent_id": req.agent_id,
            "source": req.source,
            "predecessor": req.branch_from,
            "model_override": req.model_override,
            "permission_mode": req.permission_mode,
            "permission_rules": req.permission_rules,
        })
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
        # Pretend the dispatcher persisted a user + assistant pair.
        u_id = f"u_{len(captured['calls'])}"
        a_id = f"a_{len(captured['calls'])}"
        store.append_message(req.session_id, {
            "id": u_id, "role": "user",
            "content": req.user_text,
            "timestamp": 0,
            "predecessor": req.branch_from,
            "source": req.source,
            "agent_id": req.agent_id,
        })
        store.append_message(req.session_id, {
            "id": a_id, "role": "assistant",
            "content": "(spawned reply)",
            "timestamp": 0,
            "predecessor": u_id,
            "agent_id": req.agent_id,
        })
        return _R("(spawned reply)", asst_id=a_id)

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def test_inherit_forks_off_parent_node(parent_store, fake_dispatcher):
    """inherit mode: forks off ``caller`` in the SAME session.
    No new session id is minted; no attach pointer is written by
    ``_execute_agent_turn`` itself."""
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    pre_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}

    out = _execute_agent_turn(
        session_id="p1",
        prompt="extend this",
        agent_id="probe",
        branch_from="a1",
        label="probe",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.head_id  # populated with the new assistant msg id

    # No new session id created.
    post_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}
    assert post_sessions == pre_sessions

    # The fake dispatcher saw a TurnRequest with caller pointing at a1.
    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == "p1"
    assert last["predecessor"] == "a1"
    assert last["agent_id"] == "probe"

    # No attach pointer landed automatically — that's the caller's job.
    attach_rows = [m for m in parent_store.get_messages("p1")
                   if m.get("function") == "attach"]
    assert attach_rows == []


def test_clean_starts_new_root(parent_store, fake_dispatcher):
    """clean mode: caller=None → dispatcher gets history_override=[]
    and the new turn becomes a new root in the same session."""
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    out = _execute_agent_turn(
        session_id="p1",
        prompt="independent task",
        agent_id="probe",
        branch_from=None,
        label="indie",
    )
    assert not out.failed
    assert out.head_id

    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == "p1"
    assert last["predecessor"] is None
    assert last["history_override"] == []   # clean start


def test_label_persists_as_branch_name(parent_store, fake_dispatcher):
    """A spawn with label='X' registers X as the branch name for the
    resulting head — so the right-rail Branches panel shows the label
    instead of the raw commit hash."""
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    out = _execute_agent_turn(
        session_id="p1",
        prompt="x",
        agent_id="probe",
        branch_from="a1",
        label="probe",
    )
    branches = parent_store.list_branches("p1")
    by_head = {b["head_msg_id"]: b for b in branches}
    row = by_head.get(out.head_id)
    assert row is not None, "spawn head not listed as a branch tip"
    assert row.get("name") == "probe"


def test_unknown_session_errors(parent_store, fake_dispatcher):
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn
    out = _execute_agent_turn(
        session_id="nope",
        prompt="hi",
        agent_id="main",
        branch_from="x",
    )
    assert out.failed
    assert out.error and "not found" in out.error


def test_dispatcher_failure_surfaces(parent_store, monkeypatch):
    from openprogram.runtime.conversation import turn_runtime as disp
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    def boom(req, *, on_event=None, cancel_event=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(disp, "process_user_turn", boom)

    out = _execute_agent_turn(
        session_id="p1",
        prompt="go",
        agent_id="main",
        branch_from="a1",
    )
    assert out.failed
    assert out.error and "provider exploded" in out.error


# --- spawn card identity -------------------------------------------------
#
# A synchronous spawn writes its running card before the run and the end
# write reuses that id, so the transcript updates one card in place.


def test_the_end_write_reuses_the_running_card_id(parent_store, fake_dispatcher):
    from openprogram.runtime.conversation.sub_agent_run import (
        _execute_agent_turn,
        write_attach_pointer_for_spawn,
    )

    card_id = "fixedcard01"
    result = _execute_agent_turn(
        session_id="p1", prompt="go", agent_id="worker", branch_from="a1",
    )
    assert result.head_id and not result.failed, (result.error, fake_dispatcher)
    node_id = write_attach_pointer_for_spawn(
        session_id="p1", caller_msg_id="a1", result=result,
        label="probe", prompt="go", chosen_agent="worker",
        node_id=card_id,
    )

    assert node_id == card_id


def test_the_end_write_keeps_what_the_runner_wrote_meanwhile(parent_store, monkeypatch):
    """The job runner settles the card when the job ends, on its own thread.
    Its write landing between the caller reading the card and writing it
    must survive: the caller merges into the card as it is at the write."""
    from openprogram.runtime.conversation.sub_agent_run import (
        AgentTurnResult,
        write_attach_pointer_for_spawn,
        write_attach_placeholder_for_spawn,
    )
    from openprogram.state.store.session.context_commit import store as commit_store

    card_id = write_attach_placeholder_for_spawn(
        session_id="p1", caller_msg_id="a1", label="probe", prompt="go",
        chosen_agent="worker", node_id="racecard001", job_id="j1",
        tool_call_id="call-1", delivery="tool_result",
    )
    runner_fields = {"execution_id": "j1", "status_version": 4, "result_status": "completed",
                     "usage": {"tokens": 0, "tool_calls": 0}, "source_commit_id": "commit-runner"}

    def runner_writes_meanwhile(*_args, **_kwargs):
        parent_store.merge_node("p1", card_id, lambda node: {"metadata": {
            "attach": {**node.metadata["attach"], **runner_fields, "status": "completed"}}})
        return None

    monkeypatch.setattr(commit_store, "load_commit_for_head", runner_writes_meanwhile)
    write_attach_pointer_for_spawn(
        session_id="p1", caller_msg_id="a1",
        result=AgentTurnResult(head_id="sub_a", final_text="done"),
        label="probe", prompt="go", chosen_agent="worker", node_id=card_id,
    )

    card = next(m for m in parent_store.get_messages("p1") if m.get("id") == card_id)
    attach = card["attach"]
    assert {key: attach.get(key) for key in runner_fields} == runner_fields
    assert attach["status"] == "completed" and attach["head_id"] == "sub_a"
    assert attach["tool_call_id"] == "call-1" and attach["job_id"] == "j1"


def test_write_attach_pointer_mints_id_when_not_supplied(
    parent_store, fake_dispatcher,
):
    """node_id is optional — the slash-command path still gets a
    generated id."""
    from openprogram.runtime.conversation.sub_agent_run import (
        _execute_agent_turn,
        write_attach_pointer_for_spawn,
    )

    result = _execute_agent_turn(
        session_id="p1", prompt="go", agent_id="worker", branch_from="a1",
    )
    assert result.head_id and not result.failed, (result.error, fake_dispatcher)
    node_id = write_attach_pointer_for_spawn(
        session_id="p1", caller_msg_id="a1", result=result,
        label=None, prompt="go", chosen_agent="worker",
    )
    assert node_id and len(node_id) == 12


def test_spawn_follows_session_model_pick(parent_store, fake_dispatcher):
    """A same-session spawn runs on the session's picked model: the
    picker's provider_override/model_override in session meta become
    the TurnRequest's model_override, same composition as a chat turn."""
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    parent_store.update_session(
        "p1",
        provider_override="minimax-cn-coding-plan",
        model_override="MiniMax-M3",
    )
    _execute_agent_turn(session_id="p1", prompt="go", agent_id="main",
                        branch_from="a1")
    assert fake_dispatcher["calls"][-1]["model_override"] == \
        "minimax-cn-coding-plan/MiniMax-M3"


def test_spawn_without_pick_leaves_model_to_profile(parent_store,
                                                    fake_dispatcher):
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    _execute_agent_turn(session_id="p1", prompt="go", agent_id="main",
                        branch_from="a1")
    assert fake_dispatcher["calls"][-1]["model_override"] is None


def test_a_sub_agent_with_no_parent_turn_uses_the_default(parent_store, fake_dispatcher):
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn

    _execute_agent_turn(session_id="p1", prompt="go", agent_id="main", branch_from="a1")
    assert fake_dispatcher["calls"][-1]["permission_mode"] == "bypass"


def test_a_sub_agent_inherits_its_parents_mode_and_rules(parent_store, fake_dispatcher, monkeypatch):
    from openprogram.runtime.agent.turn_request import TurnRequest, reset_turn_request, set_turn_request
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn
    from openprogram.runtime.permissions.modes import PermissionRules

    rules = PermissionRules(deny=["bash"])
    parent = TurnRequest(session_id="p1", user_text="", agent_id="main", source="cron",
                         permission_mode="ask", permission_rules=rules)
    token = set_turn_request(parent)
    try:
        _execute_agent_turn(session_id="p1", prompt="go", agent_id="main", branch_from="a1")
    finally:
        reset_turn_request(token)
    last = fake_dispatcher["calls"][-1]
    assert (last["permission_mode"], last["permission_rules"]) == ("ask", rules)


def test_a_sub_agent_stopped_before_it_starts_reports_the_stop(parent_store, fake_dispatcher, monkeypatch):
    """A stop that wins the race with the spawn's activation is a stop, not a crash."""
    from openprogram.runtime.conversation.production_driver.shared import STOPPED_BEFORE_START
    from openprogram.runtime.conversation.sub_agent_run import _execute_agent_turn
    from tests.support.stop_before_start import stop_every_turn_before_it_starts

    stopped = stop_every_turn_before_it_starts(monkeypatch)
    out = _execute_agent_turn(session_id="p1", prompt="go", agent_id="main", branch_from="a1")

    assert len(stopped) == 1
    assert fake_dispatcher["calls"] == []
    assert (out.failed, out.error, out.head_id) == (True, STOPPED_BEFORE_START, None)
