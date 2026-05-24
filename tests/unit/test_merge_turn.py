"""process_merge_turn — peer-session merge.

The merge aggregates N independent peer sessions into one new turn on
the target session, writing a multi-parent ContextCommit. No git
branches, no worktrees — just session ids.
"""
from __future__ import annotations

import json
import time

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )

    # Target session the merge writes onto.
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "kick off",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "parent_id": "u1",
    })
    s.commit_turn("p1", "parent init")

    # Two peer sessions with their own assistant replies.
    for sid, reply, label in [
        ("peer_a", "result from agent A", "A"),
        ("peer_b", "result from agent B", "B"),
    ]:
        s.create_session(sid, "main", title=label, label=label)
        s.append_message(sid, {
            "id": f"u_{sid}", "role": "user", "content": "go",
            "timestamp": 0, "parent_id": None,
        })
        s.append_message(sid, {
            "id": f"a_{sid}", "role": "assistant", "content": reply,
            "timestamp": time.time(), "parent_id": f"u_{sid}",
        })
        s.commit_turn(sid, f"{label} turn")
    return s


@pytest.fixture
def fake_dispatcher(monkeypatch):
    from openprogram.agent import dispatcher as disp

    captured: dict = {}

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

    def fake_run(req, *, on_event=None, cancel_event=None):
        captured["prompt"] = req.user_text
        captured["session_id"] = req.session_id
        captured["history_override"] = req.history_override
        from openprogram.agent.session_db import default_db
        default_db().append_message(req.session_id, {
            "id": "merge_a", "role": "assistant",
            "content": "(merged)", "parent_id": "a1",
            "timestamp": time.time(),
        })
        return _R("(merged)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def test_merges_two_peer_sessions(store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn

    out = process_merge_turn(
        target_session_id="p1",
        peers=[
            {"session_id": "peer_a"},
            {"session_id": "peer_b"},
        ],
        message="reconcile",
        agent_id="main",
    )

    assert out.error is None, out.error
    assert not out.failed
    assert out.final_text == "(merged)"
    assert out.commit_id and out.commit_id.startswith("commit_")
    # Prompt bundles both peers' final replies labeled by session.
    assert "result from agent A" in fake_dispatcher["prompt"]
    assert "result from agent B" in fake_dispatcher["prompt"]
    assert "session label=\"A\"" in fake_dispatcher["prompt"]
    assert "session label=\"B\"" in fake_dispatcher["prompt"]
    # Merge runs on the TARGET session with empty history.
    assert fake_dispatcher["session_id"] == "p1"
    assert fake_dispatcher["history_override"] == []

    # ContextCommit is written with parent_ids covering each peer's
    # latest commit id (plus any prior target commit).
    from openprogram.context.commit.store import load_commit
    commit = load_commit(store, out.commit_id, session_id="p1")
    assert commit is not None
    assert commit.parent_ids == out.parent_ids


def test_unknown_peers_drop_to_error(store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        target_session_id="p1",
        peers=[{"session_id": "never_existed"}],
        message="x",
        agent_id="main",
    )
    assert out.failed
    assert out.error and "no peer branches yielded content" in out.error


def test_unknown_target_errors(store, fake_dispatcher):
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        target_session_id="nope",
        peers=[{"session_id": "peer_a"}],
        message="x", agent_id="main",
    )
    assert out.failed
    assert out.error and "not found" in out.error


def test_same_session_two_branches_merge(store, fake_dispatcher):
    """Pass two peers with the same session_id but different head_ids
    — should merge them as if they were independent branches."""
    from openprogram.agent._merge import process_merge_turn

    # peer_a has assistant id 'a_peer_a' (fixture). Add a sibling
    # head on peer_a to play "the other branch".
    store.append_message("peer_a", {
        "id": "u_peer_a_alt", "role": "user", "content": "alternate path",
        "timestamp": 0, "parent_id": None,
    })
    store.append_message("peer_a", {
        "id": "a_peer_a_alt", "role": "assistant",
        "content": "alternate reply",
        "timestamp": 0, "parent_id": "u_peer_a_alt",
    })
    store.commit_turn("peer_a", "sibling branch")

    out = process_merge_turn(
        target_session_id="p1",
        peers=[
            {"session_id": "peer_a", "head_id": "a_peer_a"},
            {"session_id": "peer_a", "head_id": "a_peer_a_alt"},
        ],
        message="reconcile both branches",
        agent_id="main",
    )
    assert out.error is None, out.error
    assert "result from agent A" in fake_dispatcher["prompt"]
    assert "alternate reply" in fake_dispatcher["prompt"]
    # Same-session peers get disambiguated labels.
    assert "@" in fake_dispatcher["prompt"]


def test_legacy_sub_sessions_field_still_works(store, fake_dispatcher):
    """Backward-compat: callers passing ``sub_sessions=[sid, ...]``
    should still get the merge done."""
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        target_session_id="p1",
        sub_sessions=["peer_a", "peer_b"],
        message="legacy call",
        agent_id="main",
    )
    assert out.error is None, out.error
    assert not out.failed
