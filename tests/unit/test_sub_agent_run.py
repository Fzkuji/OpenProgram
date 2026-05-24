"""run_sub_agent_turn — E part 3 orchestration.

Uses a fake process_user_turn so we don't need a provider; the goal is
to verify the wiring: worktree allocation, ContextVar store override
during the call, commit on the sub-branch, and a tool_result row
written into the parent's DAG.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def parent_store(tmp_path, monkeypatch):
    """A fresh parent SessionStore bound as default_db() for this test."""
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
    """Replace process_user_turn so the test runs without a real LLM."""
    from openprogram.agent.session_db import default_db
    from openprogram.agent import dispatcher as disp

    class _R:
        def __init__(self, text, failed=False, error=None):
            self.final_text = text
            self.user_msg_id = "u"
            self.assistant_msg_id = "a"
            self.tool_calls = []
            self.usage = {}
            self.duration_ms = 1
            self.failed = failed
            self.error = error

    captured = {}

    def fake_run(req, *, on_event=None, cancel_event=None):
        # Capture the store this dispatcher invocation saw so the test
        # can assert the override is active.
        captured["seen_store"] = default_db()
        captured["session_id"] = req.session_id
        # Write something into the sub-store so commit_turn has
        # content (a regular dispatcher would write history files).
        store = default_db()
        store.append_message(req.session_id, {
            "id": "sub_u_" + req.session_id[-4:],
            "role": "user", "content": req.user_text,
            "timestamp": 0, "parent_id": None,
        })
        store.append_message(req.session_id, {
            "id": "sub_a_" + req.session_id[-4:],
            "role": "assistant", "content": "(sub-agent reply)",
            "timestamp": 0, "parent_id": "sub_u_" + req.session_id[-4:],
        })
        return _R("(sub-agent reply)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def test_run_sub_agent_turn_happy_path(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_sub_agent_turn

    result = run_sub_agent_turn(
        parent_session_id="p1",
        parent_assistant_id="a1",
        prompt="find a thing",
        agent_id="main",
        label="finder",
    )

    assert result.error is None, result.error
    assert not result.failed
    assert result.branch.startswith("sub_")
    assert "finder" in result.branch
    assert result.final_text == "(sub-agent reply)"

    # Sub-agent dispatcher saw the worktree-rooted store, not the parent's.
    assert fake_dispatcher["seen_store"] is not parent_store

    # Parent's DAG got a summary row tagged sub_agent pointing at the
    # sub-branch.
    msgs = parent_store.get_messages("p1")
    sub_rows = [m for m in msgs if m.get("function") == "sub_agent"]
    assert len(sub_rows) == 1
    row = sub_rows[0]
    assert row["parent_id"] == "a1"
    assert row["content"] == "(sub-agent reply)"
    # role must NOT be "tool" — that would trip the provider's
    # tool_call_id pairing on the next turn.
    assert row["role"] == "assistant"

    # Worktree dir was released (allocate -> use -> release).
    assert result.worktree_path is not None
    assert not result.worktree_path.exists()


def test_run_sub_agent_turn_unknown_parent(parent_store, fake_dispatcher):
    from openprogram.agent.sub_agent_run import run_sub_agent_turn
    out = run_sub_agent_turn(
        parent_session_id="nope",
        parent_assistant_id="x",
        prompt="hi", agent_id="main",
    )
    assert out.error and "not found" in out.error


def test_run_sub_agent_turn_dispatcher_failure_surfaces(parent_store, monkeypatch):
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
    # Parent DAG still got an error-marked summary row so user sees something.
    msgs = parent_store.get_messages("p1")
    sub_rows = [m for m in msgs if m.get("function") == "sub_agent"]
    assert len(sub_rows) == 1
    assert sub_rows[0].get("is_error")
