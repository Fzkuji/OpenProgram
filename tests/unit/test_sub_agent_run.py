"""run_sub_agent_turn — peer-session model.

The sub-agent is just another session in the same SessionStore; the
parent gets an attach pointer node. Tests verify (a) a fresh peer
session lands at the expected id, (b) the attach node carries the
right metadata, and (c) the parent's HEAD doesn't get pushed onto the
synthetic side child.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def parent_store(tmp_path, monkeypatch):
    from openprogram.store.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )

    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "spawn one please",
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
        })
        from openprogram.agent.session_db import default_db
        store = default_db()
        # Pretend the dispatcher persisted a user + assistant pair on
        # the sub-session so the sub-agent's session has real content.
        store.append_message(req.session_id, {
            "id": f"u_{req.session_id[-4:]}",
            "role": "user", "content": req.user_text,
            "timestamp": 0, "parent_id": None,
        })
        asst_id = f"a_{req.session_id[-4:]}"
        store.append_message(req.session_id, {
            "id": asst_id, "role": "assistant",
            "content": "(sub reply)",
            "timestamp": 0,
            "parent_id": f"u_{req.session_id[-4:]}",
        })
        return _R("(sub reply)", asst_id=asst_id)

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def test_creates_peer_session_in_same_store(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    out = run_sub_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="find a thing",
        agent_id="main",
        label="finder",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.final_text == "(sub reply)"

    # Sub-session id has the expected shape and ended up as a real
    # session in the parent's SessionStore.
    assert out.sub_session_id.startswith("sub_")
    assert "finder" in out.sub_session_id
    sess = parent_store.get_session(out.sub_session_id)
    assert sess is not None
    assert sess.get("agent_id") == "main"
    # Provenance carried on the sub-session itself.
    assert sess.get("parent_session_id") == "p1"
    assert sess.get("parent_assistant_id") == "a1"

    # Dispatcher was driven on the SUB session (not the parent).
    assert fake_dispatcher["calls"]
    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == out.sub_session_id
    assert last["history_override"] == []   # peer starts empty


def test_parent_dag_gets_one_attach_pointer(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    out = run_sub_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="x",
        agent_id="main",
        label="probe",
    )
    msgs = parent_store.get_messages("p1")
    attach_rows = [m for m in msgs if m.get("function") == "attach"]
    assert len(attach_rows) == 1
    row = attach_rows[0]
    assert row["id"] == out.attach_node_id
    assert row["role"] == "assistant"
    assert row["parent_id"] == "a1"
    # Attach metadata points at the sub-session.
    attach = row.get("attach")
    if not isinstance(attach, dict):
        extra = row.get("extra")
        if isinstance(extra, str):
            extra = json.loads(extra)
        if isinstance(extra, dict):
            attach = extra.get("attach")
    assert isinstance(attach, dict)
    assert attach["session_id"] == out.sub_session_id


def test_parent_head_does_not_advance_onto_attach(parent_store, fake_dispatcher):
    """Without HEAD preservation, the synthetic attach row would push
    parent's chat tip onto a non-conversation node and the next
    real assistant turn would walk from there."""
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    head_before = parent_store.get_session("p1").get("head_id")
    run_sub_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="y",
        agent_id="main",
    )
    head_after = parent_store.get_session("p1").get("head_id")
    assert head_after == head_before, (head_before, head_after)


def test_unknown_parent_session_errors(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_sub_agent_turn
    out = run_sub_agent_turn(
        parent_session_id="nope",
        parent_assistant_id="x",
        prompt="hi", agent_id="main",
    )
    assert out.failed
    assert out.error and "not found" in out.error


def test_dispatcher_failure_surfaces(parent_store, monkeypatch):
    from openprogram.agent import dispatcher as disp
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    def boom(req, *, on_event=None, cancel_event=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(disp, "process_user_turn", boom)

    out = run_sub_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="go", agent_id="main",
    )
    assert out.failed
    assert out.error and "provider exploded" in out.error
    # The attach pointer still landed on the parent so the user sees
    # something happened.
    msgs = parent_store.get_messages("p1")
    attach_rows = [m for m in msgs if m.get("function") == "attach"]
    assert len(attach_rows) == 1
    assert attach_rows[0].get("is_error")


def test_inline_spawn_writes_sibling_in_same_session(parent_store, fake_dispatcher):
    """run_inline_agent_turn writes a (user, assistant) pair into the
    PARENT session as a sibling fork off the given assistant id.
    No new session is created; no attach pointer is written."""
    from openprogram.agent.sub_agent_run import run_inline_agent_turn

    pre_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}

    out = run_inline_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="extend this",
        agent_id="main",
        label="probe",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.mode == "inline"
    assert out.head_id  # populated with the new assistant msg id
    assert out.sub_session_id == ""   # no new session
    assert out.attach_node_id is None  # no attach pointer

    # No NEW session id was created.
    post_sessions = {s.get("id") for s in parent_store.list_sessions(limit=999) or []}
    assert post_sessions == pre_sessions

    # The fake dispatcher saw a TurnRequest with parent_id pointing at
    # a1 — i.e. it forked off the supplied assistant message.
    last = fake_dispatcher["calls"][-1]
    assert last["session_id"] == "p1"

    # No attach pointer landed in parent's DAG.
    attach_rows = [m for m in parent_store.get_messages("p1")
                   if m.get("function") == "attach"]
    assert attach_rows == []


def test_inline_spawn_unknown_parent(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_inline_agent_turn
    out = run_inline_agent_turn(
        parent_session_id="nope",
        parent_assistant_id="x",
        prompt="hi", agent_id="main",
    )
    assert out.failed
    assert out.error and "not found" in out.error
    assert out.mode == "inline"
