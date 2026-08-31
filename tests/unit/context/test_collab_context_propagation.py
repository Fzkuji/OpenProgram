"""Collaboration ContextVars must survive the thread hops in a chain.

A chain crosses two thread boundaries the language does not carry
ContextVars across: the job runner's worker (which copies the context
explicitly) and the follow-up thread that delivers a finished job's
reply back to its dispatcher (which starts empty). What the follow-up
turn sees decides whether the message budget can ever be spent and
whether jobs spawned from it stay inside the cascade.

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
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


def _run_followup(job, monkeypatch, inside=None):
    """Fire ``_dispatch_followup`` for ``job`` and report the collaboration
    ContextVars the follow-up turn ran with. ``inside`` runs in that turn
    and its return value lands in ``seen["inside"]``."""
    from openprogram.agent.job.runner import JobRunner

    seen: dict = {}
    done = threading.Event()

    def fake_turn(req, on_event=None):
        from openprogram.agent.run_control import get_current_session_id
        from openprogram.agent.job.runner import _current_job_id
        from openprogram.programs.tools.agents.send_message.send_message.depth import (
            current_chain_generations, current_chain_messages,
        )
        seen["session_id"] = get_current_session_id()
        seen["job_id"] = _current_job_id.get()
        seen["chain_messages"] = current_chain_messages()
        seen["chain_generations"] = current_chain_generations()
        seen["req_session"] = req.session_id
        if inside is not None:
            seen["inside"] = inside(req)
        done.set()
        return types.SimpleNamespace(
            final_text="", assistant_msg_id=None, user_msg_id=None,
            failed=False, error=None,
        )

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_turn)
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    runner = JobRunner(max_workers=1)
    try:
        runner._dispatch_followup(job)
        assert done.wait(timeout=5.0), "follow-up turn never ran"
    finally:
        runner.shutdown(wait=False)
    return seen


def _job(**kw):
    from openprogram.agent.job.types import Job, JobStatus
    base = dict(id="t_child", parent_session_id="s_caller", prompt="do it",
                agent_id="main", status=JobStatus.COMPLETED,
                result_text="done", wait=False)
    base.update(kw)
    return Job(**base)


def test_followup_carries_the_chain_message_count(tmp_db, monkeypatch):
    """The reply hop keeps the chain's count, so an A↔B ping-pong spends
    the budget instead of restarting it at 0 on every reply."""
    seen = _run_followup(_job(chain_messages=3), monkeypatch)
    assert seen["chain_messages"] == 3


def test_followup_chains_jobs_to_the_dispatchers_parent(tmp_db, monkeypatch):
    """A job spawned from the follow-up turn belongs where the finished
    job belonged, so cancelling the root still reaches it."""
    seen = _run_followup(
        _job(chain_messages=1, parent_job_id="t_root"), monkeypatch,
    )
    assert seen["job_id"] == "t_root"


def test_followup_of_a_top_level_job_has_no_parent(tmp_db, monkeypatch):
    """A job spawned straight from a user turn had no parent job; its
    follow-up must not invent one."""
    seen = _run_followup(_job(chain_messages=1), monkeypatch)
    assert seen["job_id"] is None


def test_followup_runs_at_the_dispatchers_generation_count(tmp_db, monkeypatch):
    """Reading a result creates nobody, so the follow-up turn runs at the
    count the DISPATCHER had, not the finished child's."""
    seen = _run_followup(
        _job(chain_messages=1, chain_generations=1,
              caller_chain_generations=0),
        monkeypatch,
    )
    assert seen["chain_generations"] == 0
    assert seen["chain_messages"] == 1   # the message half still travels


def _seed_session(db, session_id):
    """A session with one committed turn, so ``agent`` finds a parent."""
    db.create_session(session_id, "main", title="caller")
    db.append_message(session_id, {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    db.append_message(session_id, {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    db.commit_turn(session_id, "init")


def test_followup_can_create_the_next_wave_of_agents(tmp_db, monkeypatch):
    """The shape almost every multi-agent run has: send a batch of work
    out, read what came back, send the next batch. The follow-up turn is
    where the coordinator reads the first result, so ``agent`` has to
    work there. One counter for both budgets made this impossible — the
    follow-up inherited the worker's count of 1 and every spawn in the
    chain was refused from then on.
    """
    from openprogram.agent.sub_agent_run import AgentTurnResult
    _seed_session(tmp_db, "s_caller")
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn",
        lambda **kw: AgentTurnResult(head_id="h_next",
                                     final_text="(second wave)"),
    )
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.write_attach_pointer_for_spawn",
        lambda **kw: None,
    )

    def _spawn_again(req):
        # A real follow-up turn gets these from TurnBindings; the turn
        # itself is faked out here, so bind what the tool reads.
        from openprogram.agent.run_control import set_current_session_id
        from openprogram.store import _current_turn_id
        from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl
        set_current_session_id(req.session_id)
        _current_turn_id.set("a1")
        return _agent_impl(prompt="second wave", start_from="clean")

    seen = _run_followup(
        _job(chain_messages=1, chain_generations=1,
              caller_chain_generations=0),
        monkeypatch, inside=_spawn_again,
    )
    assert "[agent refused]" not in seen["inside"]
    assert "(second wave)" in seen["inside"]


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


def test_dispatcher_releases_web_use_owner_at_turn_end(tmp_db, monkeypatch):
    from openprogram.agent.dispatcher.turn_context import TurnBindings
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.programs.workflow.browser import web_use_runtime

    released = []

    monkeypatch.setattr(
        web_use_runtime, "release_owner_if_initialized", released.append,
    )

    def _go():
        binding = TurnBindings.bind(
            req=TurnRequest(session_id="chat-1", user_text="hi",
                            agent_id="main", source="test"),
            assistant_msg_id="turn-1", db=tmp_db,
        )
        from openprogram.agent.surface_context import web_use_owner_id
        assert web_use_owner_id({"context_id": "different"}) == (
            "turn:chat-1:turn-1"
        )
        binding.release()

    _in_empty_context(_go)
    assert released == ["turn:chat-1:turn-1"]


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


def test_dispatcher_binds_and_releases_assistant_execution_id(tmp_db):
    from openprogram.agent.dispatcher.turn_context import TurnBindings
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.run_control import (
        get_current_execution_id, reset_current_execution_id,
        set_current_execution_id,
    )

    def _empty_context():
        binding = TurnBindings.bind(
            req=TurnRequest(session_id="s9", user_text="hi", agent_id="main",
                            source="test"),
            assistant_msg_id="assistant-9", db=tmp_db,
        )
        try:
            assert get_current_execution_id() == "assistant-9"
        finally:
            binding.release()
        assert get_current_execution_id() is None

    _in_empty_context(_empty_context)

    def _outer_context():
        outer_token = set_current_execution_id("outer-job")
        try:
            binding = TurnBindings.bind(
                req=TurnRequest(session_id="s9", user_text="hi", agent_id="main",
                                source="test"),
                assistant_msg_id="assistant-9", db=tmp_db,
            )
            try:
                assert get_current_execution_id() == "outer-job"
            finally:
                binding.release()
            assert get_current_execution_id() == "outer-job"
        finally:
            reset_current_execution_id(outer_token)

    _in_empty_context(_outer_context)
