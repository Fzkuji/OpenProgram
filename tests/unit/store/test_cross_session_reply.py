"""C4 — cross-session send_message reply routing.

When agent A (session A) messages a branch in session B, B's reply must be
delivered back to A's session, not B's. This is carried by the Job's
``caller_session_id`` and used by ``_dispatch_followup``.

See docs/design/runtime/agent-collaboration.md (C4).
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.agent.job.types import Job, JobStatus


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


def _runner_without_background_threads():
    import threading

    from openprogram.agent.job.runner import JobRunner

    runner = JobRunner.__new__(JobRunner)
    runner._lock = threading.Lock()
    runner._followup_locks = {}
    return runner


def test_followup_delivers_to_caller_session_when_cross(monkeypatch):
    """Cross-session: followup turn runs in caller_session_id (A), not B."""
    from openprogram.agent.job import runner as runner_mod

    seen = {}

    def fake_process(req, **kw):
        seen["session_id"] = req.session_id
        seen["text"] = req.user_text
        class _R:  # minimal TurnResult stand-in
            pass
        return _R()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)
    # set_head must not touch a real store
    from openprogram.agent import session_db as sdb
    monkeypatch.setattr(sdb, "default_db", lambda: type("S", (), {
        "set_head": staticmethod(lambda *a, **k: None)})())

    r = _runner_without_background_threads()
    job = _make_job(caller_session_id="A")  # A initiated, B ran

    import threading
    done = threading.Event()
    # Run the followup thread synchronously so we can assert.
    def run_inline(target=None, daemon=None, **kw):
        class _T:
            def start(self_):
                target()
                done.set()
        return _T()
    monkeypatch.setattr(
        runner_mod,
        "threading",
        SimpleNamespace(Thread=run_inline, Lock=runner_mod.threading.Lock),
    )

    r._dispatch_followup(job)
    assert done.is_set()
    assert seen["session_id"] == "A"           # delivered to the sender
    assert "B's answer" in seen["text"]        # reply text carried inline
    assert "回复了" in seen["text"]


def _run_followup_inline(monkeypatch, job) -> dict:
    """Fire _dispatch_followup on the calling thread and return the
    TurnRequest fields the dispatcher would have seen."""
    from openprogram.agent.job import runner as runner_mod

    seen: dict = {}

    def fake_process(req, **kw):
        seen["session_id"] = req.session_id
        seen["text"] = req.user_text
        return type("_R", (), {})()

    import openprogram.agent.dispatcher as disp
    monkeypatch.setattr(disp, "process_user_turn", fake_process)
    from openprogram.agent import session_db as sdb
    monkeypatch.setattr(sdb, "default_db", lambda: type("S", (), {
        "set_head": staticmethod(lambda *a, **k: None)})())

    def run_inline(target=None, daemon=None, **kw):
        class _T:
            def start(self_):
                target()
        return _T()
    monkeypatch.setattr(
        runner_mod,
        "threading",
        SimpleNamespace(Thread=run_inline, Lock=runner_mod.threading.Lock),
    )

    _runner_without_background_threads()._dispatch_followup(job)
    return seen


def test_followup_same_session_spawn_uses_attach(monkeypatch):
    """Same-session SPAWN (attach pointer written): delivered to
    parent_session_id, and the notice points at the attach expansion
    rather than repeating the reply."""
    job = _make_job(caller_session_id=None, caller_msg_id="cm1",
                      attach_pointer_id="ap1")
    seen = _run_followup_inline(monkeypatch, job)
    assert seen["session_id"] == "B"           # same session as it ran in
    assert "嵌在上面" in seen["text"]          # same-session attach wording
    assert "B's answer" not in seen["text"]    # context carries it, not the notice


def test_followup_cross_session_spawn_uses_source_attach(monkeypatch):
    """A cross-session spawn writes its pointer in the caller session.

    The target commit is resolved through the pointer, so carrying the same
    result inline would duplicate the child transcript in the next request.
    """
    job = _make_job(
        caller_session_id="A",
        caller_msg_id="cm1",
        attach_pointer_id="ap1",
    )
    seen = _run_followup_inline(monkeypatch, job)
    assert seen["session_id"] == "A"
    assert "嵌在上面" in seen["text"]
    assert "B's answer" not in seen["text"]


def test_followup_same_session_delivery_carries_reply_inline(monkeypatch):
    """Same-session delivery to an EXISTING branch (agent(to=…) /
    send_message) writes no attach pointer, so the reply must travel
    inline — otherwise the initiator is told the transcript is attached
    above and nothing is."""
    job = _make_job(caller_session_id="B", caller_msg_id="cm1")  # ran where it was sent
    seen = _run_followup_inline(monkeypatch, job)
    assert seen["session_id"] == "B"
    assert "B's answer" in seen["text"]        # the result actually arrives
    assert "回复了" in seen["text"]
    assert "嵌在上面" not in seen["text"]
