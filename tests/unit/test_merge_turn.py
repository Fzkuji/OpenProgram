"""process_merge_turn — F orchestration.

Fakes process_user_turn so the test runs without a provider; verifies
the wiring: sub-branch row resolution, prompt construction, multi-parent
ContextCommit write.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

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
        "id": "u1", "role": "user", "content": "do parallel work",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "spawning",
        "timestamp": 0, "parent_id": "u1",
    })
    # Two sub-agent summary rows referencing branches. Shape matches
    # what `run_sub_agent_turn` writes today: role="assistant" with
    # display="runtime" and a sub_agent metadata blob in extra.
    s.append_message("p1", {
        "id": "sub_row_A",
        "role": "assistant", "display": "runtime",
        "function": "sub_agent",
        "content": "result from agent A",
        "parent_id": "a1",
        "called_by": "a1",
        "timestamp": time.time(),
        "extra": json.dumps({
            "sub_agent": {"branch": "sub_a", "label": "A"},
        }, default=str),
    })
    s.append_message("p1", {
        "id": "sub_row_B",
        "role": "assistant", "display": "runtime",
        "function": "sub_agent",
        "content": "result from agent B",
        "parent_id": "a1",
        "called_by": "a1",
        "timestamp": time.time() + 1,
        "extra": json.dumps({
            "sub_agent": {"branch": "sub_b", "label": "B"},
        }, default=str),
    })
    s.commit_turn("p1", "setup")
    return s


@pytest.fixture
def fake_dispatcher(monkeypatch):
    """Replace process_user_turn so the test doesn't hit a provider.
    Captures the prompt the merge code built so we can assert format."""
    from openprogram.agent import dispatcher as disp

    class _R:
        def __init__(self, text):
            self.final_text = text
            self.user_msg_id = "merge_u"
            self.assistant_msg_id = "merge_a"
            self.tool_calls = []
            self.usage = {}
            self.duration_ms = 1
            self.failed = False
            self.error = None

    captured = {}

    def fake_run(req, *, on_event=None, cancel_event=None):
        captured["prompt"] = req.user_text
        captured["source"] = req.source
        # Pretend the dispatcher persisted an assistant node so
        # subsequent load_commit_for_head can find a head_node.
        from openprogram.agent.session_db import default_db
        store = default_db()
        store.append_message(req.session_id, {
            "id": "merge_a", "role": "assistant",
            "content": "(merged)", "parent_id": "a1",
            "timestamp": time.time(),
        })
        return _R("(merged)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def _git_branch(parent_store, sid: str, name: str) -> None:
    """Create a branch in the parent's repo so rev-parse resolves."""
    pair = parent_store._open(sid)
    assert pair
    git, _ = pair
    git._ensure_init()
    git._run("branch", name, "HEAD", check=False)


def test_merge_turn_writes_multi_parent_commit(parent_store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn

    _git_branch(parent_store, "p1", "sub_a")
    _git_branch(parent_store, "p1", "sub_b")

    out = process_merge_turn(
        parent_session_id="p1",
        sub_branches=["sub_a", "sub_b"],
        message="please reconcile",
        agent_id="main",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.final_text == "(merged)"
    assert out.parent_node_id == "merge_a"
    assert out.commit_id and out.commit_id.startswith("commit_")
    # Two sub-branch SHAs landed in parent_ids (parent-branch commit may or
    # may not be present depending on whether a prior commit existed for
    # the parent head).
    assert len(out.parent_ids) >= 2

    # The merge prompt actually mentioned both branches.
    assert "branch name=\"sub_a\"" in fake_dispatcher["prompt"]
    assert "branch name=\"sub_b\"" in fake_dispatcher["prompt"]
    assert "result from agent A" in fake_dispatcher["prompt"]
    assert "result from agent B" in fake_dispatcher["prompt"]

    # ContextCommit got persisted with the right parent_ids.
    from openprogram.context.commit.store import load_commit
    commit = load_commit(parent_store, out.commit_id, session_id="p1")
    assert commit is not None
    assert commit.parent_ids == out.parent_ids
    assert commit.head_node_id == "merge_a"


def test_merge_turn_unknown_branch(parent_store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        parent_session_id="p1",
        sub_branches=["sub_never_existed"],
        message="x",
        agent_id="main",
    )
    assert out.failed
    assert out.error and "no sub-agent rows" in out.error


def test_merge_turn_unknown_parent(parent_store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        parent_session_id="nope",
        sub_branches=["sub_a"],
        message="x", agent_id="main",
    )
    assert out.failed and "not found" in (out.error or "")
