"""list_sessions / list_branches — C2 discovery tools.

See docs/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

import pytest

from openprogram.functions.tools.agent_collab.list_branches import (
    _list_sessions_impl as list_sessions,
    _list_branches_impl as list_branches,
    _clip,
)


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

    from openprogram.webui import _pause_stop
    tok = _pause_stop._current_session_id.set("p1")
    yield s
    _pause_stop._current_session_id.reset(tok)


def test_clip():
    assert _clip("a" * 100).endswith("…")
    assert _clip("short") == "short"
    assert _clip("line1\nline2") == "line1 line2"


def test_list_sessions_lists_both(two_sessions):
    out = list_sessions()
    assert "p1" in out and "p2" in out
    assert "first" in out and "second" in out
    assert "[main]" in out and "[research]" in out


def test_list_sessions_marks_current(two_sessions):
    out = list_sessions()
    # p1 is the current session
    p1_line = next(ln for ln in out.splitlines() if ln.startswith("- p1"))
    assert "← current" in p1_line


def test_list_sessions_emits_event(two_sessions):
    from openprogram.agent.event_bus import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(lambda e: got.append(e),
                                      types={"sessions.listed"})
    try:
        list_sessions()
    finally:
        unsub()
    assert any(e.type == "sessions.listed" for e in got)


def test_list_branches_gives_target(two_sessions):
    out = list_branches("p1")
    # the line must carry a ready-to-use target=p1:HEAD
    assert "target=p1:" in out


def test_list_branches_defaults_to_current(two_sessions):
    out = list_branches()  # no arg → current session p1
    assert "target=p1:" in out


def test_list_branches_emits_event(two_sessions):
    from openprogram.agent.event_bus import get_event_bus
    got = []
    unsub = get_event_bus().subscribe(lambda e: got.append(e),
                                      types={"branches.listed"})
    try:
        list_branches("p1")
    finally:
        unsub()
    ev = next(e for e in got if e.type == "branches.listed")
    assert ev.payload["session"] == "p1"


def test_list_branches_no_session_errors(monkeypatch):
    from openprogram.webui import _pause_stop
    tok = _pause_stop._current_session_id.set(None)
    try:
        out = list_branches()
    finally:
        _pause_stop._current_session_id.reset(tok)
    assert "no session_id" in out
