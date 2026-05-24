"""task tool — Claude-Code-style sub-agent spawn from inside a turn."""
from __future__ import annotations

from contextvars import copy_context
from pathlib import Path

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
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "parent_id": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "parent_id": "u1",
    })
    s.commit_turn("p1", "init")
    return s


@pytest.fixture
def fake_dispatcher(monkeypatch):
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
        captured["prompt"] = req.user_text
        captured["agent_id"] = req.agent_id
        store = __import__(
            "openprogram.agent.session_db", fromlist=["default_db"],
        ).default_db()
        store.append_message(req.session_id, {
            "id": "u_" + req.session_id[-4:],
            "role": "user", "content": req.user_text,
            "timestamp": 0, "parent_id": None,
        })
        store.append_message(req.session_id, {
            "id": "a_" + req.session_id[-4:],
            "role": "assistant", "content": "(sub reply)",
            "timestamp": 0, "parent_id": "u_" + req.session_id[-4:],
        })
        return _R("(sub reply)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def _call_task(*, prompt: str, description: str = "", agent_id: str = "",
               session_id: str | None = None, turn_id: str | None = None):
    """Invoke the task tool's underlying Python (skipping the @function
    wrapper which is for LLM-facing dispatch). ContextVars must be set
    so _resolve_parent finds them."""
    from openprogram.webui._pause_stop import _current_session_id
    from openprogram.store import _current_turn_id
    from openprogram.functions.tools.task.task import _task_impl

    def _go():
        tok1 = _current_session_id.set(session_id)
        tok2 = _current_turn_id.set(turn_id)
        try:
            return _task_impl(
                prompt=prompt, description=description, agent_id=agent_id,
            )
        finally:
            _current_session_id.reset(tok1)
            _current_turn_id.reset(tok2)

    return copy_context().run(_go)


def test_task_returns_subagent_text(store, fake_dispatcher):
    out = _call_task(
        prompt="find the answer", description="finder",
        session_id="p1", turn_id="a1",
    )
    assert "(sub reply)" in out
    assert "[sub-agent branch=" in out
    assert "finder" in out  # label encoded in branch name
    # The fake dispatcher saw the prompt verbatim.
    assert fake_dispatcher["prompt"] == "find the answer"


def test_task_resolves_parent_agent_when_not_supplied(store, fake_dispatcher):
    _call_task(
        prompt="x", session_id="p1", turn_id="a1",
    )
    assert fake_dispatcher["agent_id"] == "main"


def test_task_explicit_agent_id_wins(store, fake_dispatcher):
    _call_task(
        prompt="x", agent_id="researcher",
        session_id="p1", turn_id="a1",
    )
    assert fake_dispatcher["agent_id"] == "researcher"


def test_task_without_session_returns_error(store, fake_dispatcher):
    out = _call_task(prompt="x", session_id=None, turn_id="a1")
    assert "[task error]" in out
    assert "no active parent turn" in out


def test_task_without_turn_returns_error(store, fake_dispatcher):
    out = _call_task(prompt="x", session_id="p1", turn_id=None)
    assert "[task error]" in out
    assert "no active parent turn" in out


def test_task_sanitizes_description_for_branch_name(store, fake_dispatcher):
    out = _call_task(
        prompt="x", description="my/dangerous label!@#",
        session_id="p1", turn_id="a1",
    )
    # Non [A-Za-z0-9_-] chars get replaced with _ in the branch label.
    # The resulting branch name should appear in the output and contain
    # only safe chars (we don't pin the exact form).
    import re
    m = re.search(r"branch=(\S+)", out)
    assert m is not None
    branch = m.group(1).rstrip(']')
    assert all(c.isalnum() or c in "_-" for c in branch), branch
