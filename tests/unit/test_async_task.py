"""Async task lifecycle — state machine, store, runner.

Tests the parts that don't require a real LLM. The runner submits a
fake ``run_agent_turn`` so the worker exercises real cancel + status
flows but stops in milliseconds.

Scenarios covered (per docs/design/runtime/async-task-lifecycle.md D13):

  * state machine transitions: legal + illegal edges
  * persistence + round-trip via tasks.json
  * runner.spawn_task end-to-end with a fake worker
  * cancel signal propagation (cancel during pending and during run)
  * crash recovery: reconcile_orphans flips non-terminal → errored
  * pool backpressure: tasks queue up beyond max_workers
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore + session row for task tests."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session_store.default_store", lambda: s,
    )
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: s,
    )
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {
        "id": "u1", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    s.append_message("p1", {
        "id": "a1", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u1",
    })
    s.commit_turn("p1", "init")
    return s


def test_state_machine_legal_edges():
    from openprogram.agent.task.types import TaskStatus, can_transition
    assert can_transition(TaskStatus.PENDING, TaskStatus.QUEUED)
    assert can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.ERRORED)
    assert can_transition(TaskStatus.PENDING, TaskStatus.CANCELLED)


def test_state_machine_illegal_edges():
    from openprogram.agent.task.types import TaskStatus, can_transition
    # Terminal states are absorbing.
    assert not can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    assert not can_transition(TaskStatus.CANCELLED, TaskStatus.RUNNING)
    assert not can_transition(TaskStatus.ERRORED, TaskStatus.COMPLETED)
    # Can't skip back to earlier non-terminal.
    assert not can_transition(TaskStatus.RUNNING, TaskStatus.PENDING)
    assert not can_transition(TaskStatus.QUEUED, TaskStatus.PENDING)


def test_task_to_dict_roundtrip():
    from openprogram.agent.task.types import Task, TaskStatus
    t = Task(
        id="t_abc", parent_session_id="s1", prompt="hi", agent_id="main",
        label="alpha", subject="alpha",
    )
    d = t.to_dict()
    assert d["status"] == "pending"
    t2 = Task.from_dict(d)
    assert t2.id == "t_abc"
    assert t2.status == TaskStatus.PENDING
    assert t2.label == "alpha"


def test_store_save_load(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, load_task, list_tasks

    t = Task(
        id="t_one", parent_session_id="p1", prompt="x", agent_id="main",
        subject="one",
    )
    save_task("p1", t)
    loaded = load_task("p1", "t_one")
    assert loaded is not None
    assert loaded.id == "t_one"
    assert loaded.status == TaskStatus.PENDING

    rows = list_tasks("p1")
    assert len(rows) == 1
    assert rows[0].id == "t_one"


def test_store_update_status_legal_transition(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, update_task_status
    t = Task(id="t_a", parent_session_id="p1", prompt="x", agent_id="main")
    save_task("p1", t)
    updated = update_task_status("p1", "t_a", TaskStatus.QUEUED)
    assert updated.status == TaskStatus.QUEUED
    assert updated.queued_at is not None


def test_store_update_status_illegal_transition_raises(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, update_task_status
    t = Task(
        id="t_b", parent_session_id="p1", prompt="x", agent_id="main",
        status=TaskStatus.COMPLETED,
    )
    save_task("p1", t)
    with pytest.raises(ValueError):
        update_task_status("p1", "t_b", TaskStatus.RUNNING)


def test_store_reconcile_orphans_flips_running_to_errored(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, load_task, reconcile_orphans
    t = Task(
        id="t_orphan", parent_session_id="p1", prompt="x", agent_id="main",
        status=TaskStatus.RUNNING,
    )
    save_task("p1", t)
    n = reconcile_orphans()
    assert n == 1
    cur = load_task("p1", "t_orphan")
    assert cur.status == TaskStatus.ERRORED
    assert "died" in (cur.error or "")


def test_store_reconcile_orphans_preserves_terminal(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, load_task, reconcile_orphans
    t = Task(
        id="t_done", parent_session_id="p1", prompt="x", agent_id="main",
        status=TaskStatus.COMPLETED,
    )
    save_task("p1", t)
    reconcile_orphans()
    cur = load_task("p1", "t_done")
    assert cur.status == TaskStatus.COMPLETED


# Runner tests

@pytest.fixture
def fake_worker(monkeypatch):
    """Replace run_agent_turn with a deterministic fake that records
    every invocation and respects the cancel event."""
    calls = []
    barrier = threading.Event()  # release worker when set
    cancel_seen = threading.Event()  # set inside fake when ev fires
    entered = threading.Event()  # set once the worker is INSIDE fake_run

    def fake_run(*, session_id, prompt, agent_id, branch_from=None, label=None, spawn_caller=None):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.webui._pause_stop import is_cancelled
        calls.append({
            "session_id": session_id, "prompt": prompt,
            "agent_id": agent_id, "branch_from": branch_from, "label": label,
        })
        # Signal "worker is past the pending→running transition and
        # actually executing fake_run". Tests that want to cancel
        # mid-run wait on this before calling cancel_task — otherwise
        # the runner can flip pending→cancelled before the worker
        # picks up the future and the worker body never runs.
        entered.set()
        # Wait either for barrier OR for cancel — whichever comes first.
        for _ in range(50):
            if barrier.is_set():
                break
            if is_cancelled(session_id):
                cancel_seen.set()
                return AgentTurnResult(head_id="head_x", final_text="",
                                       failed=True, error="cancelled")
            time.sleep(0.02)
        return AgentTurnResult(head_id="head_ok", final_text="hello",
                               failed=False, error=None)

    import openprogram.agent.task.runner as runner_mod
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run,
    )
    yield calls, barrier, cancel_seen, entered
    # Cleanup any singleton runner so the next test gets a fresh pool.
    runner_mod.shutdown_runner()


def test_runner_spawn_completes(store_fixture, fake_worker, monkeypatch):
    # Silence ws broadcasts inside tests (no real server running).
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    tid = runner.spawn_task(
        session_id="p1", prompt="do thing", agent_id="main",
        parent_msg_id="a1", label="alpha",
    )
    barrier.set()
    final = runner.await_task(tid, timeout=5.0)
    assert final is not None
    assert final.status == TaskStatus.COMPLETED
    assert final.result_text == "hello"
    assert final.head_id == "head_ok"
    assert len(calls) == 1
    assert calls[0]["prompt"] == "do thing"
    assert calls[0]["branch_from"] == "a1"


def test_runner_cancel_before_pickup(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    # Force a single-worker pool occupied by another task to keep the
    # second one queued; cancel the queued one. Use two different
    # sessions so the session-level cancel event for the queued task
    # doesn't bleed into the running one (cancel is session-scoped
    # per D5 of the design).
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "1")
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()
    calls, barrier, _, _ = fake_worker

    # Second session for the queued+cancelled task.
    store_fixture.create_session("p2", "main", title="parent2")
    store_fixture.append_message("p2", {
        "id": "u2", "role": "user", "content": "hi",
        "timestamp": 0, "predecessor": None,
    })
    store_fixture.append_message("p2", {
        "id": "a2", "role": "assistant", "content": "ok",
        "timestamp": 0, "predecessor": "u2",
    })
    store_fixture.commit_turn("p2", "init")

    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    tid1 = runner.spawn_task(
        session_id="p1", prompt="block", agent_id="main",
        parent_msg_id="a1",
    )
    tid2 = runner.spawn_task(
        session_id="p2", prompt="cancel me", agent_id="main",
        parent_msg_id="a2",
    )
    # tid1 occupies the worker (waiting on barrier). tid2 sits in
    # queued. Cancel tid2 before it gets picked up.
    time.sleep(0.05)
    res = runner.cancel_task(tid2)
    assert res is not None
    assert res.status in (TaskStatus.CANCELLED, TaskStatus.ERRORED)
    barrier.set()
    final = runner.await_task(tid1, timeout=5.0)
    assert final.status == TaskStatus.COMPLETED


def test_runner_cancel_during_run(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, cancel_seen, entered = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    tid = runner.spawn_task(
        session_id="p1", prompt="will be cancelled", agent_id="main",
        parent_msg_id="a1",
    )
    # Wait until the worker is actually executing fake_run before
    # cancelling — otherwise cancel_task can flip the task to
    # cancelled while it's still pending, _run_one's
    # pending→running transition gets rejected, and fake_run never
    # gets a chance to observe the cancel signal.
    assert entered.wait(timeout=2.0), "fake worker never started"
    # Don't release barrier — cancel mid-run.
    runner.cancel_task(tid)
    final = runner.await_task(tid, timeout=5.0)
    assert final is not None
    assert final.status in (TaskStatus.CANCELLED, TaskStatus.ERRORED)
    # Worker observed cancel (via is_cancelled flag).
    assert cancel_seen.is_set()


def test_runner_pool_backpressure(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    monkeypatch.setenv("OPENPROGRAM_TASK_WORKERS", "1")
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()

    calls, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    ids = [
        runner.spawn_task(
            session_id="p1", prompt=f"n{i}", agent_id="main",
            parent_msg_id="a1",
        )
        for i in range(3)
    ]
    # Single worker occupied; others queued.
    time.sleep(0.05)
    statuses = [runner.get_task(t).status for t in ids]
    # First either pending/queued/running, later ones should not be running.
    running = [s for s in statuses if s == TaskStatus.RUNNING]
    assert len(running) <= 1
    # Now drain.
    barrier.set()
    for t in ids:
        final = runner.await_task(t, timeout=5.0)
        assert final.status in (TaskStatus.COMPLETED, TaskStatus.ERRORED)
