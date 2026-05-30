"""task tool — same-session spawn from inside a turn."""
from __future__ import annotations

from contextvars import copy_context

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.store.session.session_store import SessionStore
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
        captured["parent_id"] = req.parent_id
        captured["history_override"] = req.history_override
        from openprogram.agent.session_db import default_db
        s = default_db()
        u_id = "u_" + str(len(captured))
        a_id = "a_" + str(len(captured))
        s.append_message(req.session_id, {
            "id": u_id, "role": "user", "content": req.user_text,
            "timestamp": 0, "parent_id": req.parent_id,
            "agent_id": req.agent_id,
        })
        s.append_message(req.session_id, {
            "id": a_id, "role": "assistant", "content": "(spawned reply)",
            "timestamp": 0, "parent_id": u_id,
            "agent_id": req.agent_id,
        })
        return _R("(spawned reply)")

    monkeypatch.setattr(disp, "process_user_turn", fake_run)
    return captured


def _call_task(*, prompt: str, description: str = "", agent_id: str = "",
               context: str = "inherit",
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
                context=context,
            )
        finally:
            _current_session_id.reset(tok1)
            _current_turn_id.reset(tok2)

    return copy_context().run(_go)


def test_task_inherit_default(store, fake_dispatcher):
    """Default context=inherit: forks off the caller turn, history
    inherited. Result string carries ``branch=<sid>:<head_id>``."""
    out = _call_task(
        prompt="find the answer", description="finder",
        session_id="p1", turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["prompt"] == "find the answer"
    # inherit → parent_id pinned to the caller turn.
    assert fake_dispatcher["parent_id"] == "a1"
    # inherit → history_override left at default (None) so dispatcher
    # walks the conv chain ending at a1.
    assert fake_dispatcher["history_override"] is None


def test_task_clean_mode_starts_new_root(store, fake_dispatcher):
    """context=clean: new root (parent_id=None) in the same session.
    Result string still carries branch=<sid>:<head_id> — same session."""
    out = _call_task(
        prompt="find the answer", description="finder", context="clean",
        session_id="p1", turn_id="a1",
    )
    assert "(spawned reply)" in out
    assert "[spawned agent branch=p1:" in out
    assert fake_dispatcher["parent_id"] is None
    assert fake_dispatcher["history_override"] == []   # empty start


def test_task_resolves_parent_agent_when_not_supplied(store, fake_dispatcher):
    _call_task(prompt="x", session_id="p1", turn_id="a1")
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


def test_task_unknown_context_returns_error(store, fake_dispatcher):
    out = _call_task(
        prompt="x", context="weird",
        session_id="p1", turn_id="a1",
    )
    assert "[task error]" in out
    assert "unknown context" in out
