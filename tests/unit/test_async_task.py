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


class _FakeMonotonic:
    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


@pytest.fixture
def store_fixture(tmp_path, monkeypatch):
    """Isolated SessionStore + session row for task tests."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: s,
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


def test_store_reconcile_orphans_legacy_only_preserves_governed_tasks(store_fixture):
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.agent.task.store import save_task, load_task, reconcile_orphans
    save_task(
        "p1",
        Task(
            id="legacy", parent_session_id="p1", prompt="x", agent_id="main",
            status=TaskStatus.RUNNING,
        ),
    )
    save_task(
        "p1",
        Task(
            id="governed", parent_session_id="p1", prompt="x", agent_id="main",
            status=TaskStatus.QUEUED, admission_id="adm_governed",
        ),
    )

    assert reconcile_orphans(legacy_only=True) == 1
    assert load_task("p1", "legacy").status == TaskStatus.ERRORED
    assert load_task("p1", "governed").status == TaskStatus.QUEUED


# Runner tests

@pytest.fixture
def fake_worker(monkeypatch):
    """Replace run_agent_turn with a deterministic fake that records
    every invocation and respects the cancel event."""
    calls = []
    barrier = threading.Event()  # release worker when set
    cancel_seen = threading.Event()  # set inside fake when ev fires
    entered = threading.Event()  # set once the worker is INSIDE fake_run

    def fake_run(*, session_id, prompt, agent_id, branch_from=None, label=None, spawn_caller=None, advance_head=True):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.agent.run_control import is_cancelled
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


def test_runner_spawn_persists_durable_admission(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import ResourceGovernor, ResourceLimits, resolve_resource_limits
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    ledger = UsageLedger(tmp_path / "governance.db")
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    renewals = []
    original_renew = governor.renew_lease

    def record_renewal(*args, **kwargs):
        renewals.append(args[0])
        return original_renew(*args, **kwargs)

    monkeypatch.setattr(governor, "renew_lease", record_renewal)
    monkeypatch.setattr("openprogram.agent.task.runner._LEASE_RENEW_SECS", 0.02)
    runner = TaskRunner(
        max_workers=1,
        governor=governor,
    )
    try:
        tid = runner.spawn_task(
            session_id="p1", prompt="governed", agent_id="main", parent_msg_id="a1",
        )
        task = runner.get_task(tid)
        assert task is not None
        assert task.admission_id
        assert task.budget_scope_id
        assert task.effective_limits["max_live_per_session"] == 1
        assert fake_worker[3].wait(2)
        assert ledger.connection().execute(
            "SELECT state FROM task_admissions WHERE task_id = ?", (tid,),
        ).fetchone()[0] == "live"
        deadline = time.time() + 1
        while not renewals and time.time() < deadline:
            time.sleep(0.01)
        assert renewals and set(renewals) == {tid}
        fake_worker[1].set()
        assert runner.await_task(tid, timeout=5).status.value == "completed"
        assert ledger.connection().execute(
            "SELECT state FROM task_admissions WHERE task_id = ?", (tid,),
        ).fetchone()[0] == "released"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runner_rejection_creates_no_task(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        AdmissionRejected,
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_tasks_per_session=1), scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        runner.spawn_task(session_id="p1", prompt="one", agent_id="main")
        with pytest.raises(AdmissionRejected) as caught:
            runner.spawn_task(session_id="p1", prompt="two", agent_id="main")
        assert caught.value.decision.reason_code == "quota.tasks_exhausted"
        assert [task.prompt for task in runner.list_tasks("p1")] == ["one"]
    finally:
        fake_worker[1].set()
        runner.shutdown()


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


def test_runner_durable_dispatcher_skips_saturated_session(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    store_fixture.create_session("p2", "main", title="parent2")
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=2,
    )
    runner = TaskRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        runner.spawn_task(session_id="p1", prompt="p1 first", agent_id="main")
        runner.spawn_task(session_id="p1", prompt="p1 second", agent_id="main")
        runner.spawn_task(session_id="p2", prompt="p2 first", agent_id="main")

        deadline = time.time() + 1.0
        while len(fake_worker[0]) < 2 and time.time() < deadline:
            time.sleep(0.01)

        assert {call["session_id"] for call in fake_worker[0]} == {"p1", "p2"}
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_queued_cancel_does_not_cancel_unrelated_session_runtime(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    store_fixture.create_session("p2", "main", title="parent2")
    cancelled_sessions = []
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled", cancelled_sessions.append,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.kill_active_runtime", lambda _sid: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        runner.spawn_task(session_id="p1", prompt="live", agent_id="main")
        queued = runner.spawn_task(
            session_id="p2", prompt="queued", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)

        runner.cancel_task(queued)

        assert cancelled_sessions == []
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runner_restart_dispatches_persisted_governed_queue(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.store import save_task
    from openprogram.agent.task.types import Task, TaskStatus
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "governance.db"),
        limit_resolver=lambda _sid, _task: resolved,
    )
    task = Task(
        id="restart_queued", parent_session_id="p1",
        prompt="resume me", agent_id="main",
    )
    governor.admit_task(task, persist=lambda accepted: save_task("p1", accepted))

    runner = TaskRunner(max_workers=1, governor=governor)
    try:
        assert fake_worker[3].wait(1.0)
        fake_worker[1].set()
        assert runner.await_task(task.id, timeout=5).status == TaskStatus.COMPLETED
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_worker_lost_fence_prevents_stale_runner_from_writing_completed(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.store import load_task
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved)
    runner = TaskRunner(max_workers=1, governor=governor)
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="stale", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        ledger.connection().execute(
            "UPDATE task_admissions SET lease_expires_at = 0 WHERE task_id = ?",
            (task_id,),
        )
        ledger.connection().commit()

        reconciled = governor.reconcile(
            task_lookup=lambda session_id, current_task_id: load_task(
                session_id, current_task_id,
            ),
            mark_worker_lost=runner._mark_worker_lost,
            owner_is_alive=lambda _owner: False,
            now=1,
        )
        fake_worker[1].set()
        final = runner.await_task(task_id, timeout=5)

        assert reconciled.released_worker_lost == 1
        assert final.status == TaskStatus.ERRORED
        assert final.reason_code == "error.worker_lost"
        assert final.result_text in (None, "")
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runner_treats_its_own_instance_as_live_without_lock_probe(
    store_fixture, fake_worker, tmp_path,
):
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        assert runner._owner_holds_worker_lock(runner._instance_id) is True
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runtime_budget_moves_live_task_to_stopping_until_worker_exits(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    entered = threading.Event()
    release = threading.Event()

    def stubborn_run(**_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        entered.set()
        release.wait(2.0)
        return AgentTurnResult(
            head_id="head", final_text="late", failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", stubborn_run,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=1), scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger, limit_resolver=lambda _sid, _task: resolved),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="stubborn", agent_id="main",
        )
        assert entered.wait(1.0)
        clock.advance(1.1)
        deadline = time.time() + 1.0
        state = None
        while time.time() < deadline:
            state = ledger.connection().execute(
                "SELECT state FROM task_admissions WHERE task_id = ?", (task_id,),
            ).fetchone()[0]
            if state == "stopping":
                break
            time.sleep(0.01)

        assert state == "stopping"
        assert runner.get_task(task_id).status == TaskStatus.RUNNING
        release.set()
        final = runner.await_task(task_id, timeout=5)
        assert final.status == TaskStatus.CANCELLED
        assert final.reason_code == "budget.runtime_exhausted"
    finally:
        release.set()
        runner.shutdown()


def test_idle_budget_resets_only_after_meaningful_activity(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="idle", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        clock.advance(0.75)
        assert runner.record_task_activity(task_id, "transport_keepalive") is False
        assert runner.record_task_activity(task_id, "provider_data") is True
        clock.advance(0.75)
        time.sleep(0.05)
        assert runner.get_task(task_id).status == TaskStatus.RUNNING
        clock.advance(0.30)
        final = runner.await_task(task_id, timeout=5)
        assert final.status == TaskStatus.CANCELLED
        assert final.reason_code == "budget.idle_exhausted"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_bounded_operation_timeout_clamps_and_rejects_unbounded_strict_work(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=4),
        scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="bounded", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)

        assert runner.bounded_operation_timeout(task_id, 8.0) == 4.0
        clock.advance(3.0)
        assert runner.bounded_operation_timeout(task_id, 8.0) == 1.0
        assert runner.bounded_operation_timeout(task_id, None) == 1.0
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runner_releases_bookkeeping_after_completion(store_fixture, fake_worker,
                                                      monkeypatch):
    """Both _tasks AND _done_events must be emptied once a task ends.

    _done_events used to be written on spawn and never popped, leaking
    one threading.Event per task for the process lifetime. Popping it
    is safe because await_task grabs its reference before waiting.
    """
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    _, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    barrier.set()  # let workers run straight through
    ids = [
        runner.spawn_task(
            session_id="p1", prompt=f"n{i}", agent_id="main",
            parent_msg_id="a1",
        )
        for i in range(5)
    ]
    for t in ids:
        assert runner.await_task(t, timeout=5.0).status == TaskStatus.COMPLETED
    # Give the finally-block a beat to run after the last status write.
    for _ in range(50):
        if not runner._tasks and not runner._done_events:
            break
        time.sleep(0.02)
    assert runner._tasks == {}, "task entries leaked"
    assert runner._done_events == {}, "done-events leaked"


def test_runner_await_after_completion_still_returns(store_fixture, fake_worker,
                                                     monkeypatch):
    """await_task on an already-finished task works with no done-event.

    Once _done_events is popped, a late waiter falls through to the
    terminal-status check and returns immediately.
    """
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    _, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    barrier.set()
    tid = runner.spawn_task(
        session_id="p1", prompt="quick", agent_id="main", parent_msg_id="a1",
    )
    assert runner.await_task(tid, timeout=5.0).status == TaskStatus.COMPLETED
    # Second await, long after the event was dropped, must not hang.
    started = time.time()
    again = runner.await_task(tid, timeout=5.0)
    assert again.status == TaskStatus.COMPLETED
    assert time.time() - started < 1.0
