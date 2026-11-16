"""Background agent → completion notice → caller context, end to end.

Runs the real job runner, terminal hook, notice store, notice sweep and
canonical admission of the notice turn; only the two model turns are
faked (the sub-agent's and the caller's). Checks what the caller would
see: one automated notice turn, and a context holding the sub-agent's
report instead of its transcript.
"""
from __future__ import annotations

import atexit
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def runner_lifecycle(monkeypatch):
    import openprogram.runtime.jobs.runner as runner_mod

    runner_mod.shutdown_runner()
    monkeypatch.setattr("openprogram.operations.worker.lock.is_held_by", lambda _pid: True)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.shared._broadcast", lambda *a, **k: None)
    yield
    runner_mod.shutdown_runner()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.runtime.context import session_db as sdb_mod
    from openprogram.runtime.jobs import notices

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: s)
    monkeypatch.setattr(notices, "_due", set())
    monkeypatch.setattr(notices, "_seeded", True)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "check the logs",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "started a scout",
                            "timestamp": 0, "predecessor": "u1"})
    s.commit_turn("p1", "init")
    try:
        yield s
    finally:
        timer = s._index_timer
        s._flush_index()
        if timer is not None:
            timer.join(timeout=1)
        atexit.unregister(s._flush_index)


def test_background_agent_reports_back_once_with_only_its_report(store, monkeypatch):
    from openprogram.runtime.jobs import get_runner, JobStatus, notices
    from openprogram.runtime.conversation.peer_agents.sub_agent_run import AgentTurnResult, write_attach_placeholder_for_spawn

    from openprogram.runtime.jobs.types import mint_job_id
    # As agent() writes it: the card names its Job from the start.
    job_id = mint_job_id()
    attach_id = write_attach_placeholder_for_spawn(
        session_id="p1", caller_msg_id="a1", label="scout",
        prompt="read the worker log", chosen_agent="main", job_id=job_id,
    )

    seen: dict = {}
    done = threading.Event()

    def model_turn(*, request, cancel_event, on_event=None, **_kwargs):
        if request.source == "job_followup":
            # The caller's notice turn: record what it was asked, and
            # whether it streams to the open chat.
            seen["req"] = request
            seen["on_event"] = on_event
            done.set()
            return AgentTurnResult(head_id=None, final_text="noted")
        # The child's transcript: a long tool-heavy run the caller must not see.
        store.append_message(request.session_id, {
            "id": "child_tool", "role": "tool", "name": "bash",
            "content": "TOOL NOISE " * 50, "predecessor": request.branch_from,
            "timestamp": time.time()})
        store.append_message(request.session_id, {
            "id": "child_head", "role": "assistant", "content": "the log is clean",
            "predecessor": "child_tool", "timestamp": time.time()})
        store.commit_turn(request.session_id, "child turn")
        return AgentTurnResult(head_id="child_head", final_text="the log is clean")

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(model_turn),
    )
    runner = get_runner()
    job_id = runner.spawn_job(
        job_id=job_id,
        session_id="p1", prompt="read the worker log", agent_id="main",
        subject="scout", description="read the worker log",
        parent_msg_id="a1", label="scout", attach_pointer_id=attach_id, wait=False,
    )
    final = runner.await_job(job_id, timeout=5.0)
    assert final is not None and final.status == JobStatus.COMPLETED

    # The terminal hook queued exactly one notice for the caller.
    path = notices._path("p1")
    assert [e["job_id"] for e in notices._load(path)[0]] == [job_id]

    # The sweep admits the caller's notice turn through canonical admission.
    notices.sweep()
    done.wait(10.0)
    assert done.is_set(), "notice turn never ran"
    req = seen["req"]
    assert req.session_id == "p1" and req.source == "job_followup"
    assert "automated notice, not a message from the user" in req.user_text
    assert f"Agent \"scout\" finished (job {job_id})." in req.user_text
    # The card stays as written (the caller's prompt only grows by
    # appending), so the report travels in the notice, without the child's
    # transcript.
    assert "the log is clean" in req.user_text and "attached above" not in req.user_text
    assert "TOOL NOISE" not in req.user_text
    assert seen["on_event"] is not None, "the notice turn must stream to the chat"
    assert [i["kind"] for i in req.notice] == ["report"]

    # Delivered once: neither another sweep nor a repeated terminal hook notifies again.
    assert notices._load(path)[0] == []
    runner._update_attach_card(runner.get_job(job_id) or final)
    assert notices._load(path)[0] == []

    # The caller's line holds the card, not the child's transcript; the card
    # is one whose prompt text stays as written (render._attach_report).
    from openprogram.runtime.context.compaction.persistence import rendered_history
    rows = rendered_history(store, "p1", head_id=attach_id)
    assert "TOOL NOISE" not in repr(rows)
    card = next(row for row in rows if row.get("id") == attach_id)
    assert card["attach"]["result_in"] == "notice" and card["attach"]["status"] == "completed"
