"""C4 — cross-session send_message reply routing.

When agent A (session A) messages a branch in session B, B's reply must be
delivered back to A's session, not B's. This is carried by the Job's
``caller_session_id`` and used by ``_dispatch_followup``.

The follow-up turn is admitted through the canonical Agent adapter, which
freezes the request against the session store and records it as a durable
execution. These tests run that admission for real against a session store
and an execution store in the test's own folders, and read back the input
that was admitted. Only the activation that would call a model is replaced:
it records what was admitted instead of running the turn.

See docs/reference/design/runtime/collaboration/agent-collaboration.html (C4).
"""
from __future__ import annotations

import threading

import pytest

from openprogram.runtime.jobs.types import Job, JobStatus


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """A real session store in this test's folder, with sessions A and B."""
    from openprogram.runtime.context.session_db import SessionDB

    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    db.create_session("A", "main", title="sender")
    db.create_session("B", "main", title="target")
    return db


def _make_job(**kw):
    base = dict(
        id="t_x",
        parent_session_id="B",   # the job ran in session B (target)
        prompt="do thing",
        agent_id="main",
        status=JobStatus.COMPLETED,
        head_id="head_b",
        result_text="B's answer",
    )
    base.update(kw)
    return Job(**base)


def _admitted_followup(monkeypatch, job) -> dict:
    """Fire ``_dispatch_followup`` and return the chat request the canonical
    adapter admitted for the notice turn, read back from the execution store."""
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.jobs.runner import JobRunner

    admitted: dict = {}
    done = threading.Event()

    async def record_admission(self, admission):
        stored = self.store.get_agent_turn_input(admission.execution_id)
        admitted["execution_session"] = admission.session_id
        admitted["request"] = dict(stored["request"])
        done.set()
        return None

    monkeypatch.setattr(CanonicalAgentAdapter, "activate", record_admission)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.shared._broadcast", lambda *a, **k: None)
    runner = JobRunner(max_workers=1)
    try:
        runner._dispatch_followup(job)
        assert done.wait(timeout=30), "the notice turn was never admitted"
    finally:
        runner.shutdown(wait=False)
    return admitted


def test_followup_delivers_to_caller_session_when_cross(sessions, monkeypatch):
    """Cross-session delivery to an existing branch: the notice turn runs in
    caller_session_id (A), not B, and carries B's reply inline."""
    seen = _admitted_followup(monkeypatch, _make_job(caller_session_id="A"))
    assert seen["execution_session"] == "A"    # delivered to the sender
    assert seen["request"]["session_id"] == "A"
    assert seen["request"]["source"] == "job_followup"
    text = seen["request"]["user_text"]
    assert "B's answer" in text                # reply text carried inline
    assert "Reply from branch B:head_b" in text


def _card(db, session_id: str, *, current: bool) -> None:
    """The spawn's attach card ``ap1`` in ``session_id``: as agent() writes
    it now (its result travels in the notice), or as one written before."""
    if current:
        from openprogram.runtime.conversation.peer_agents.sub_agent_run import write_attach_placeholder_for_spawn
        assert write_attach_placeholder_for_spawn(
            session_id=session_id, caller_msg_id=None, label="t_x", prompt="do thing",
            chosen_agent="main", node_id="ap1", job_id="t_x", target_session_id="B") == "ap1"
        return
    import json
    db.append_message(session_id, {
        "id": "ap1", "role": "assistant", "display": "runtime", "function": "attach",
        "content": "B's answer", "predecessor": None, "timestamp": 0.0,
        "extra": json.dumps({"attach": {"session_id": "B", "job_id": "t_x", "status": "completed"}}),
    }, advance_head=False)
    db.commit_turn(session_id, "old card")


@pytest.mark.parametrize("caller", [None, "A"])
def test_followup_spawn_carries_its_result_in_the_notice(sessions, monkeypatch, caller):
    """A spawn's card (in the caller's session, same or other) stays as it
    was written, so the notice carries the result; nothing else does."""
    _card(sessions, caller or "B", current=True)
    job = _make_job(caller_session_id=caller, caller_msg_id="cm1", attach_pointer_id="ap1", wait=False)
    seen = _admitted_followup(monkeypatch, job)
    assert seen["execution_session"] == (caller or "B")
    text = seen["request"]["user_text"]
    assert "B's answer" in text
    assert "attached above" not in text


@pytest.mark.parametrize("caller", [None, "A"])
def test_followup_spawn_with_a_card_from_before_points_at_it(sessions, monkeypatch, caller):
    """A card written before results moved into the notice renders its own
    report, so the notice points at it rather than repeating the reply."""
    _card(sessions, caller or "B", current=False)
    job = _make_job(caller_session_id=caller, caller_msg_id="cm1", attach_pointer_id="ap1", wait=False)
    seen = _admitted_followup(monkeypatch, job)
    text = seen["request"]["user_text"]
    assert "attached above" in text
    assert "B's answer" not in text


def test_followup_same_session_delivery_carries_reply_inline(sessions, monkeypatch):
    """Same-session delivery to an EXISTING branch (agent(to=…) /
    send_message) writes no attach pointer, so the reply must travel
    inline — otherwise the initiator is told the transcript is attached
    above and nothing is."""
    job = _make_job(caller_session_id="B", caller_msg_id="cm1")  # ran where it was sent
    seen = _admitted_followup(monkeypatch, job)
    assert seen["execution_session"] == "B"
    text = seen["request"]["user_text"]
    assert "B's answer" in text                # the result actually arrives
    assert "Reply from branch B:head_b" in text
    assert "attached above" not in text
