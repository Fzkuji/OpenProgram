"""Cascading cancel — cancel_job stops the whole parent_job_id subtree.

Covers (design: agent-collaboration.md §5.3):
  * cancelling a parent flips its pending/queued children to cancelled
    before they ever run
  * a running child receives the cancel signal through the normal
    per-job cancel path
  * grandchildren stop too (recursion over the chain)
  * a cycle in parent_job_id does not hang the walk (visited guard)
  * spawns made inside a running job record parent_job_id
  * session-level cancel clears the target's inbox and leaves a system
    notice in each sender session

Fake-worker technique mirrors tests/component/agent/async_job_support.py.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _worker_lock_is_held(monkeypatch):
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
    )


@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore with three session rows (parent / child /
    grandchild lanes)."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    for sid in ("p1", "p2", "p3"):
        s.create_session(sid, "main", title=sid)
        s.append_message(sid, {
            "id": f"u_{sid}", "role": "user", "content": "hi",
            "timestamp": 0, "predecessor": None,
        })
        s.append_message(sid, {
            "id": f"a_{sid}", "role": "assistant", "content": "ok",
            "timestamp": 0, "predecessor": f"u_{sid}",
        })
        s.commit_turn(sid, "init")
    return s


@pytest.fixture
def fake_worker(monkeypatch):
    """Deterministic run_agent_turn stand-in: blocks on a barrier,
    drops out when the session-level cancel flag fires."""
    calls = []
    barrier = threading.Event()
    cancel_seen = threading.Event()

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.agent.run_control import is_cancelled
        calls.append({"session_id": session_id, "prompt": prompt})
        for _ in range(100):
            if barrier.is_set():
                break
            if is_cancelled(session_id):
                cancel_seen.set()
                return AgentTurnResult(head_id="head_x", final_text="",
                                       failed=True, error="cancelled")
            time.sleep(0.02)
        return AgentTurnResult(head_id="head_ok", final_text="done",
                               failed=False, error=None)

    import openprogram.agent.job.runner as runner_mod
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_run,
    )
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    yield calls, barrier, cancel_seen
    runner_mod.shutdown_runner()


def _wait_status(runner, tid, statuses, timeout=5.0):
    from openprogram.agent.job.types import JobStatus  # noqa: F401
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = runner.get_job(tid)
        if t is not None and t.status in statuses:
            return t
        time.sleep(0.02)
    return runner.get_job(tid)


def test_parent_cancel_dequeues_pending_child(store_fixture, fake_worker,
                                              monkeypatch):
    """Pending child (never picked up) flips to cancelled with the parent."""
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    calls, barrier, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()

    parent = runner.spawn_job(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    # Wait until the parent occupies the single worker.
    _wait_status(runner, parent, {JobStatus.RUNNING})
    child = runner.spawn_job(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_job_id=parent,
    )
    runner.cancel_job(parent)
    p = runner.await_job(parent, timeout=5.0)
    c = runner.await_job(child, timeout=5.0)
    assert p.status == JobStatus.CANCELLED
    assert c.status == JobStatus.CANCELLED
    # The child never ran.
    assert all(call["prompt"] != "child" for call in calls)


def test_pending_child_cannot_slip_into_the_freed_slot(
        store_fixture, fake_worker, monkeypatch):
    """The cascade reaches descendants before the root releases its worker.

    Cancelling the root makes its worker drop out, which frees a pool
    slot; the pool immediately starts the next queued future, which is
    the child the cascade was on its way to cancel. Slowing the walk down
    turns that interleaving from an occasional flake into every run.
    """
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    calls, _barrier, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()

    walk = runner._descendant_jobs
    monkeypatch.setattr(
        runner, "_descendant_jobs",
        lambda root: (time.sleep(0.2), walk(root))[1],
    )

    parent = runner.spawn_job(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    _wait_status(runner, parent, {JobStatus.RUNNING})
    child = runner.spawn_job(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_job_id=parent,
    )
    runner.cancel_job(parent)
    runner.await_job(parent, timeout=5.0)
    assert runner.await_job(child, timeout=5.0).status == JobStatus.CANCELLED
    assert all(call["prompt"] != "child" for call in calls), calls


def test_parent_cancel_stops_running_child(store_fixture, fake_worker,
                                           monkeypatch):
    """Running child (different session) is cancelled through the normal
    per-job cancel path."""
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "2")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    _, barrier, cancel_seen = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()

    parent = runner.spawn_job(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    child = runner.spawn_job(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_job_id=parent,
    )
    assert _wait_status(runner, parent, {JobStatus.RUNNING}).status == JobStatus.RUNNING
    assert _wait_status(runner, child, {JobStatus.RUNNING}).status == JobStatus.RUNNING

    runner.cancel_job(parent)
    p = runner.await_job(parent, timeout=5.0)
    c = runner.await_job(child, timeout=5.0)
    assert p.status == JobStatus.CANCELLED
    assert c.status == JobStatus.CANCELLED
    assert cancel_seen.is_set()


def test_cancel_request_does_not_resurrect_a_finished_job(
        store_fixture, fake_worker):
    """The cancel-request stamp must not blind-write a stale snapshot.

    ``_cancel_single`` reads the job, then stamps ``cancel_requested_at``
    on it. The worker it signalled two lines earlier can reach its own
    terminal write inside that window; a blind ``save_job`` of the stale
    snapshot rewrote ``status: running`` over the worker's ``cancelled``
    and pinned the job non-terminal forever. Forcing the stale read
    makes the race deterministic — it is otherwise a rare interleaving
    that only shows up as an occasional ``running != cancelled``.
    """
    import openprogram.agent.job.runner as runner_mod
    from openprogram.agent.job import get_runner, JobStatus
    from openprogram.agent.job.store import (
        load_job, save_job, update_job_status,
    )
    from openprogram.agent.job.types import Job

    # Build the runner FIRST: its startup reconcile errors any
    # pre-existing non-terminal job.
    runner = get_runner()
    save_job("p1", Job(id="t_resurrect", parent_session_id="p1",
                         prompt="x", agent_id="main",
                         status=JobStatus.RUNNING))
    stale = load_job("p1", "t_resurrect")
    # The worker wins the race and writes its terminal status.
    update_job_status("p1", "t_resurrect", JobStatus.CANCELLED)

    # Plain setattr, not the monkeypatch fixture: undoing it has to be
    # scoped to this one call, and monkeypatch.undo() would also drop
    # store_fixture's / fake_worker's patches.
    original = runner_mod._store_load
    runner_mod._store_load = (
        lambda sid, tid: stale if tid == "t_resurrect" else original(sid, tid)
    )
    try:
        runner._cancel_single("t_resurrect")
    finally:
        runner_mod._store_load = original

    assert load_job("p1", "t_resurrect").status == JobStatus.CANCELLED


def test_grandchild_stops_too(store_fixture, fake_worker, monkeypatch):
    """Recursion: parent → child → grandchild, all cancelled from the root."""
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    _, barrier, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()

    parent = runner.spawn_job(
        session_id="p1", prompt="parent", agent_id="main",
        parent_msg_id="a_p1",
    )
    _wait_status(runner, parent, {JobStatus.RUNNING})
    child = runner.spawn_job(
        session_id="p2", prompt="child", agent_id="main",
        parent_msg_id="a_p2", parent_job_id=parent,
    )
    grandchild = runner.spawn_job(
        session_id="p3", prompt="grandchild", agent_id="main",
        parent_msg_id="a_p3", parent_job_id=child,
    )
    runner.cancel_job(parent)
    for tid in (parent, child, grandchild):
        final = runner.await_job(tid, timeout=5.0)
        assert final.status == JobStatus.CANCELLED, tid


def test_cycle_in_parent_chain_does_not_hang(store_fixture, fake_worker):
    """A (theoretically impossible) parent_job_id cycle terminates."""
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.agent.job.store import save_job
    from openprogram.agent.job import get_runner

    # Construct the runner FIRST — its startup reconcile flips any
    # pre-existing non-terminal job to errored.
    runner = get_runner()
    a = Job(id="t_cyc_a", parent_session_id="p1", prompt="a",
             agent_id="main", parent_job_id="t_cyc_b")
    b = Job(id="t_cyc_b", parent_session_id="p1", prompt="b",
             agent_id="main", parent_job_id="t_cyc_a")
    save_job("p1", a)
    save_job("p1", b)
    done = threading.Event()
    result = {}

    def _go():
        result["job"] = runner.cancel_job("t_cyc_a")
        done.set()

    threading.Thread(target=_go, daemon=True).start()
    assert done.wait(timeout=5.0), "cancel_job hung on a parent cycle"
    assert result["job"].status == JobStatus.CANCELLED
    assert runner.get_job("t_cyc_b").status == JobStatus.CANCELLED


def test_spawn_inside_job_records_parent(store_fixture, fake_worker,
                                          monkeypatch):
    """A spawn made from inside a running job defaults parent_job_id
    to that job (the producer side of the cascade chain)."""
    import openprogram.agent.job.runner as runner_mod
    from openprogram.agent.job import get_runner
    runner = get_runner()
    recorded = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        # Simulate only the outer turn's tool call. Letting the same fake
        # spawn from the child would replace this direct-child id with a
        # recursively created descendant before the assertion runs.
        if prompt == "outer":
            recorded["child_id"] = runner.spawn_job(
                session_id="p2", prompt="inner child", agent_id="main",
                parent_msg_id="a_p2",
            )
        return AgentTurnResult(head_id="h", final_text="ok",
                               failed=False, error=None)

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_run,
    )
    parent = runner.spawn_job(
        session_id="p1", prompt="outer", agent_id="main",
        parent_msg_id="a_p1",
    )
    runner.await_job(parent, timeout=5.0)
    assert "child_id" in recorded
    child_job = runner.await_job(recorded["child_id"], timeout=5.0)
    assert child_job.parent_job_id == parent


def test_concurrent_spawns_keep_their_own_parent(store_fixture, fake_worker,
                                                  monkeypatch):
    """Concurrent parent turns do not exchange their ambient job ids."""
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "4")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.job import get_runner
    from openprogram.agent.sub_agent_run import AgentTurnResult

    runner = get_runner()
    parents_ready = threading.Barrier(2)
    children = {}

    def fake_run(*, session_id, prompt, agent_id, branch_from=None,
                 label=None, spawn_caller=None, advance_head=True):
        if prompt in {"outer-1", "outer-2"}:
            parents_ready.wait(timeout=5.0)
            child_session = "p2" if prompt == "outer-1" else "p3"
            children[prompt] = runner.spawn_job(
                session_id=child_session,
                prompt=f"child-of-{prompt}",
                agent_id="main",
                parent_msg_id=f"a_{child_session}",
            )
        return AgentTurnResult(
            head_id="h", final_text="ok", failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_run,
    )
    parent_1 = runner.spawn_job(
        session_id="p1", prompt="outer-1", agent_id="main",
        parent_msg_id="a_p1",
    )
    parent_2 = runner.spawn_job(
        session_id="p2", prompt="outer-2", agent_id="main",
        parent_msg_id="a_p2",
    )
    runner.await_job(parent_1, timeout=5.0)
    runner.await_job(parent_2, timeout=5.0)

    child_1 = runner.await_job(children["outer-1"], timeout=5.0)
    child_2 = runner.await_job(children["outer-2"], timeout=5.0)
    assert child_1.parent_job_id == parent_1
    assert child_2.parent_job_id == parent_2


def test_worker_clears_job_context_after_execution(store_fixture, fake_worker,
                                                    monkeypatch):
    """The executor thread retains no job identity after ``_run_one``."""
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    from openprogram.agent.job import get_runner

    runner = get_runner()
    job_id = runner.spawn_job(
        session_id="p1", prompt="one job", agent_id="main",
        parent_msg_id="a_p1",
    )
    runner.await_job(job_id, timeout=5.0)

    def inspect_worker_context():
        return (
            runner_mod._current_job_id.get(),
            runner_mod._current_job_runner.get(),
        )

    assert runner._pool.submit(inspect_worker_context).result(timeout=5.0) == (
        None,
        None,
    )


def test_session_cancel_clears_inbox_with_sender_notice(store_fixture):
    """Session-level cancel drops queued messages and notifies senders."""
    from openprogram.agent import inbox

    inbox.enqueue(
        "p2",
        message="queued while busy",
        sender_session_id="p1",
        sender_msg_id="a_p1",
        sender_agent_id="main",
        agent_id="main",
        chain_messages=0,
        target_head_id="a_p2",
    )
    assert inbox.pending_count("p2") == 1

    cleared = inbox.clear("p2", reason="the target session was stopped by the user")
    assert cleared == 1
    assert inbox.pending_count("p2") == 0

    # Sender session got a runtime-display notice, head untouched.
    msgs = store_fixture.get_messages("p1") or []
    notices = [m for m in msgs if "discarded" in (m.get("content") or "")]
    assert len(notices) == 1
    assert "queued while busy" in notices[0]["content"]
    assert (store_fixture.get_session("p1") or {}).get("head_id") == "a_p1"

    # Idempotent: clearing an empty inbox is a no-op.
    assert inbox.clear("p2") == 0
