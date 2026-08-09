"""Collaboration ContextVars must survive the thread hops in a chain.

A chain crosses two thread boundaries the language does not carry
ContextVars across: the task runner's worker (which copies the context
explicitly) and the follow-up thread that delivers a finished task's
reply back to its dispatcher (which starts empty). What the follow-up
turn sees decides whether the message budget can ever be spent and
whether tasks spawned from it stay inside the cascade.

Design: docs/reference/design/runtime/agent-collaboration.md §5.1, §5.3.
"""
from __future__ import annotations

import contextvars
import threading
import types

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


def _run_followup(task, monkeypatch):
    """Fire ``_dispatch_followup`` for ``task`` and report the collaboration
    ContextVars the follow-up turn ran with."""
    from openprogram.agent.task.runner import TaskRunner

    seen: dict = {}
    done = threading.Event()

    def fake_turn(req, on_event=None):
        from openprogram.agent.run_control import get_current_session_id
        from openprogram.agent.task.runner import _current_task_id
        from openprogram.functions.tools.send_message.send_message.depth import (
            current_chain_messages,
        )
        seen["session_id"] = get_current_session_id()
        seen["task_id"] = _current_task_id.get()
        seen["chain_messages"] = current_chain_messages()
        seen["req_session"] = req.session_id
        done.set()
        return types.SimpleNamespace(
            final_text="", assistant_msg_id=None, user_msg_id=None,
            failed=False, error=None,
        )

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_turn)
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    runner = TaskRunner(max_workers=1)
    try:
        runner._dispatch_followup(task)
        assert done.wait(timeout=5.0), "follow-up turn never ran"
    finally:
        runner.shutdown(wait=False)
    return seen


def _task(**kw):
    from openprogram.agent.task.types import Task, TaskStatus
    base = dict(id="t_child", parent_session_id="s_caller", prompt="do it",
                agent_id="main", status=TaskStatus.COMPLETED,
                result_text="done", wait=False)
    base.update(kw)
    return Task(**base)


def test_followup_carries_the_chain_message_count(tmp_db, monkeypatch):
    """The reply hop keeps the chain's count, so an A↔B ping-pong spends
    the budget instead of restarting it at 0 on every reply."""
    seen = _run_followup(_task(chain_messages=3), monkeypatch)
    assert seen["chain_messages"] == 3


def test_followup_chains_tasks_to_the_dispatchers_parent(tmp_db, monkeypatch):
    """A task spawned from the follow-up turn belongs where the finished
    task belonged, so cancelling the root still reaches it."""
    seen = _run_followup(
        _task(chain_messages=1, parent_task_id="t_root"), monkeypatch,
    )
    assert seen["task_id"] == "t_root"


def test_followup_of_a_top_level_task_has_no_parent(tmp_db, monkeypatch):
    """A task spawned straight from a user turn had no parent task; its
    follow-up must not invent one."""
    seen = _run_followup(_task(chain_messages=1), monkeypatch)
    assert seen["task_id"] is None


def _in_empty_context(fn):
    """Run ``fn`` with every ContextVar at its default — what a freshly
    started thread gets, without the thread."""
    return contextvars.Context().run(fn)


def test_dispatcher_binds_the_session_id_when_nothing_did(tmp_db):
    """``agent`` / ``send_message`` read the session id from the ambient
    ContextVar and say the dispatcher binds it. Where nothing bound it
    (the follow-up thread, merge, the CLI goal turn) it has to."""
    from openprogram.agent.dispatcher.turn_context import TurnBindings
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.run_control import get_current_session_id

    def _go():
        b = TurnBindings.bind(
            req=TurnRequest(session_id="s9", user_text="hi",
                            agent_id="main", source="test"),
            assistant_msg_id="a9", db=tmp_db,
        )
        inside = get_current_session_id()
        b.release()
        return inside, get_current_session_id()

    assert _in_empty_context(_go) == ("s9", None)


def test_dispatcher_leaves_an_outer_binding_alone(tmp_db):
    """An entry point that bound the id for a scope wider than the turn
    stays in charge: a nested turn for another session must not repoint
    the cancel hook or runtime.ask at a session with no turn token."""
    from openprogram.agent.dispatcher.turn_context import TurnBindings
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.run_control import (
        get_current_session_id, set_current_session_id,
    )

    def _go():
        set_current_session_id("s_outer")
        b = TurnBindings.bind(
            req=TurnRequest(session_id="s_inner", user_text="hi",
                            agent_id="main", source="test"),
            assistant_msg_id="a1", db=tmp_db,
        )
        inside = get_current_session_id()
        b.release()
        return inside, get_current_session_id()

    assert _in_empty_context(_go) == ("s_outer", "s_outer")
