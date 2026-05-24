"""Sub-agent workspace allocator (E part 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.sub_agent import allocate_sub_agent, release_sub_agent


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_db", lambda: s)
    s.create_session("sess1", "chat", title="t")
    s.append_message("sess1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("sess1", {
        "id": "a1", "role": "assistant", "content": "hello",
        "timestamp": 0, "parent_id": "u1",
    })
    s.commit_turn("sess1", "init turn")
    return s


def test_allocate_returns_path_and_unique_branch(store):
    ws1 = allocate_sub_agent("sess1", "a1", label="searchA")
    ws2 = allocate_sub_agent("sess1", "a1", label="searchA")
    assert ws1 and ws2
    assert ws1.branch != ws2.branch
    assert ws1.path.exists() and ws2.path.exists()
    assert ws1.path != ws2.path


def test_allocate_unknown_session(store):
    assert allocate_sub_agent("nope", "a1") is None


def test_release_removes_path(store):
    ws = allocate_sub_agent("sess1", "a1")
    assert ws and ws.path.exists()
    release_sub_agent(ws)
    assert not ws.path.exists()


def test_subagent_edits_isolated_from_parent_workdir(store):
    ws = allocate_sub_agent("sess1", "a1", label="writer")
    assert ws
    (ws.path / "workdir" / "child.txt").parent.mkdir(parents=True, exist_ok=True)
    (ws.path / "workdir" / "child.txt").write_text("from sub-agent")
    parent_wd = store.session_workdir("sess1")
    assert parent_wd is not None
    assert not (parent_wd / "child.txt").exists()
