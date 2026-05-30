"""run_agent_turn — same-session multi-agent.

Spawn is just another branch (or a new root) in the parent session's
DAG. There is no separate sub_session id and no attach pointer
written by ``run_agent_turn`` itself — those are decisions for the
caller (e.g. ``_run_spawn`` in the webui broadcasts a result; merge
writes explicit multi-parent commits).

Tests:
  * inherit mode forks off a given node (same session) and stamps
    ``agent_id`` on the new turn's metadata.
  * clean mode writes a new root (``parent_id=null``) in the same
    session.
  * unknown session is reported as a structured failure.
  * dispatcher failure is surfaced (not swallowed).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def parent_store(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )

    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "first turn",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "parent_id": "u1",
    })
    s.commit_turn("p1", "initial parent turn")
    return s


@pytest.fixture
def fake_dispatcher(monkeypatch):
    from openprogram.agent import dispatcher as disp

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
            "parent_id": req.parent_id,
        })
        from openprogram.agent.session_db import default_db
        store = default_db()
        # Pretend the dispatcher persisted a user + assistant pair.
        u_id = f"u_{len(captured['calls'])}"
        a_id = f"a_{len(captured['calls'])}"
        store.append_message(req.session_id, {
            "id": u_id, "role": "user",
            "content": req.user_text,
            "timestamp": 0,
            "parent_id": req.parent_id,
            "agent_id": req.agent_id,
        })
        store.append_message(req.session_id, {
            "id": a_id, "role": "assistant",
            "content": "(spawned reply)",
            "timestamp": 0,
            "parent_id": u_id,
            "agent_id": req.agent_id,
        })
        return _R("(spawned reply)", asst_id=a_id)

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def test_inherit_forks_off_parent_node(parent_store, fake_dispatcher):
    """inherit mode: forks off ``parent_id`` in the SAME session.
    No new session id is minted; no attach pointer is written by
    ``run_agent_turn`` itself."""
    from openprogram.agent.sub_agent_run import run_agent_turn

    pre_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}

    out = run_agent_turn(
        session_id="p1",
        prompt="extend this",
        agent_id="probe",
        parent_id="a1",
        label="probe",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.head_id  # populated with the new assistant msg id

    # No new session id created.
    post_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}
    assert post_sessions == pre_sessions

    # The fake dispatcher saw a TurnRequest with parent_id pointing at a1.
    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == "p1"
    assert last["parent_id"] == "a1"
    assert last["agent_id"] == "probe"

    # No attach pointer landed automatically — that's the caller's job.
    attach_rows = [m for m in parent_store.get_messages("p1")
                   if m.get("function") == "attach"]
    assert attach_rows == []


def test_clean_starts_new_root(parent_store, fake_dispatcher):
    """clean mode: parent_id=None → dispatcher gets history_override=[]
    and the new turn becomes a new root in the same session."""
    from openprogram.agent.sub_agent_run import run_agent_turn

    out = run_agent_turn(
        session_id="p1",
        prompt="independent task",
        agent_id="probe",
        parent_id=None,
        label="indie",
    )
    assert not out.failed
    assert out.head_id

    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == "p1"
    assert last["parent_id"] is None
    assert last["history_override"] == []   # clean start


def test_label_persists_as_branch_name(parent_store, fake_dispatcher):
    """A spawn with label='X' registers X as the branch name for the
    resulting head — so the right-rail Branches panel shows the label
    instead of the raw commit hash."""
    from openprogram.agent.sub_agent_run import run_agent_turn

    out = run_agent_turn(
        session_id="p1",
        prompt="x",
        agent_id="probe",
        parent_id="a1",
        label="probe",
    )
    branches = parent_store.list_branches("p1")
    by_head = {b["head_msg_id"]: b for b in branches}
    row = by_head.get(out.head_id)
    assert row is not None, "spawn head not listed as a branch tip"
    assert row.get("name") == "probe"


def test_unknown_session_errors(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_agent_turn
    out = run_agent_turn(
        session_id="nope",
        prompt="hi",
        agent_id="main",
        parent_id="x",
    )
    assert out.failed
    assert out.error and "not found" in out.error


def test_dispatcher_failure_surfaces(parent_store, monkeypatch):
    from openprogram.agent import dispatcher as disp
    from openprogram.agent.sub_agent_run import run_agent_turn

    def boom(req, *, on_event=None, cancel_event=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(disp, "process_user_turn", boom)

    out = run_agent_turn(
        session_id="p1",
        prompt="go",
        agent_id="main",
        parent_id="a1",
    )
    assert out.failed
    assert out.error and "provider exploded" in out.error
