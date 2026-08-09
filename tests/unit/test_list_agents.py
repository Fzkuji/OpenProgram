"""list_agents — the discovery tool for branch-to-branch communication.

See docs/reference/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

import pytest

from openprogram.functions.tools.send_message.list_agents.list_agents import (
    _list_agents_impl as list_agents,
)
from openprogram.functions.tools.send_message.shared import _clip


@pytest.fixture
def two_sessions(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)

    s.create_session("p1", "main", title="first")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hello one",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant",
                            "content": "answer one", "timestamp": 1,
                            "predecessor": "u1"})
    s.commit_turn("p1", "t1")

    s.create_session("p2", "research", title="second")
    s.append_message("p2", {"id": "u2", "role": "user", "content": "hello two",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p2", {"id": "a2", "role": "assistant",
                            "content": "answer two", "timestamp": 1,
                            "predecessor": "u2"})
    s.commit_turn("p2", "t2")

    from openprogram.agent import run_control
    tok = run_control._current_session_id.set("p1")
    yield s
    run_control._current_session_id.reset(tok)


def test_clip():
    assert _clip("a" * 100).endswith("…")
    assert _clip("short") == "short"
    assert _clip("line1\nline2") == "line1 line2"


def test_list_agents_lists_both_sessions(two_sessions):
    out = list_agents(scope="all")
    assert "p1" in out and "p2" in out
    assert "first" in out and "second" in out
    assert "[main]" in out and "[research]" in out


def test_list_agents_marks_current_session(two_sessions):
    out = list_agents(scope="all")
    p1_line = next(ln for ln in out.splitlines() if ln.startswith("p1"))
    assert "← current" in p1_line


def test_list_agents_gives_to_per_branch(two_sessions):
    out = list_agents(scope="all")
    # every session's branches carry a ready-to-use to=SID:HEAD
    assert "to=p1:" in out
    assert "to=p2:" in out


def test_list_agents_shows_branch_stats(two_sessions):
    out = list_agents()
    # p1's branch: 2 turns, "hello one" + "answer one" = 19 chars < 1000
    p1_branch = next(ln for ln in out.splitlines()
                     if ln.strip().startswith("- to=p1:"))
    assert "— 2 turns, <1k chars" in p1_branch


def test_list_agents_stats_kilochars(two_sessions):
    s = two_sessions
    s.append_message("p1", {"id": "u3", "role": "user", "content": "x" * 3000,
                            "timestamp": 2, "predecessor": "a1"})
    s.commit_turn("p1", "t3")
    out = list_agents()
    p1_branch = next(ln for ln in out.splitlines()
                     if ln.strip().startswith("- to=p1:"))
    assert "3 turns" in p1_branch
    assert "~3k chars" in p1_branch


def test_list_agents_emits_event(two_sessions):
    from openprogram.events import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(lambda e: got.append(e),
                                      types={"agents.listed"})
    try:
        list_agents(scope="all")
    finally:
        unsub()
    ev = next(e for e in got if e.type == "agents.listed")
    assert ev.payload["sessions"] == 2
    assert ev.payload["branches"] >= 2


def test_list_agents_default_scope_is_current_session_only(two_sessions):
    out = list_agents()
    assert "to=p1:" in out
    assert "p2" not in out


def test_list_agents_default_scope_keeps_preview(two_sessions):
    out = list_agents()
    assert "answer one" in out


def test_list_agents_all_scope_has_no_preview(two_sessions):
    out = list_agents(scope="all")
    assert "answer one" not in out and "answer two" not in out


def test_list_agents_all_scope_limit(two_sessions):
    out = list_agents(scope="all", limit=1)
    # list_sessions is most-recently-active first; only one session shows
    assert out.count("to=") == 1
    assert "1 session(s)" in out
