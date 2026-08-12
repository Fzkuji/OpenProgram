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
    monkeypatch.setattr(
        "openprogram.worker.lock.is_held_by", lambda _pid: True,
        raising=False,
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
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_run,
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


def test_runner_resumes_deferred_task_at_current_target_head(
    store_fixture, fake_worker, monkeypatch,
):
    """A busy-target Task keeps one admission while its target advances."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    task_id = runner.spawn_task(
        task_id="t_deferred",
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a1",
        caller_msg_id="caller",
        creates_agent=False,
        defer_dispatch=True,
    )
    assert not fake_worker[3].wait(0.8), "deferred task ran before inbox delivery"

    resumed = runner.spawn_task(
        task_id=task_id,
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a2",
        caller_msg_id="caller",
        creates_agent=False,
        resume_deferred=True,
    )
    barrier.set()
    final = runner.await_task(resumed, timeout=5.0)

    assert final is not None
    assert final.status == TaskStatus.COMPLETED
    assert calls[-1]["branch_from"] == "a2"


def test_deferred_resume_recovers_after_ready_publish_crash(
    store_fixture, fake_worker, monkeypatch,
):
    """Retry finishes the same fenced resume after Task save succeeds."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.task import get_runner, TaskStatus
    runner = get_runner()
    task_id = runner.spawn_task(
        task_id="t_resume_crash",
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a1",
        caller_msg_id="caller",
        creates_agent=False,
        defer_dispatch=True,
    )
    real_mark_ready = runner._governor.mark_dispatch_ready
    attempts = 0

    def crash_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SystemExit("crash after Task save")
        return real_mark_ready(*args, **kwargs)

    monkeypatch.setattr(runner._governor, "mark_dispatch_ready", crash_once)
    with pytest.raises(SystemExit, match="crash after Task save"):
        runner.spawn_task(
            task_id=task_id,
            session_id="p1",
            prompt="queued delivery",
            agent_id="main",
            parent_msg_id="a2",
            caller_msg_id="caller",
            creates_agent=False,
            resume_deferred=True,
        )

    resumed = runner.spawn_task(
        task_id=task_id,
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a2",
        caller_msg_id="caller",
        creates_agent=False,
        resume_deferred=True,
    )
    barrier.set()
    final = runner.await_task(resumed, timeout=5.0)

    assert attempts == 2
    assert final is not None
    assert final.status == TaskStatus.COMPLETED
    assert calls[-1]["branch_from"] == "a2"
    admission = runner._governor.ledger.connection().execute(
        "SELECT COUNT(*), state, resume_parent_msg_id "
        "FROM task_admissions WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert tuple(admission) == (1, "released", None)


def test_runner_recovers_deferred_inbox_after_process_crash(
    store_fixture, monkeypatch,
):
    """A crash after admission but before inbox publish is recoverable."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import inbox
    from openprogram.agent.task import get_runner
    import openprogram.agent.task.runner as runner_mod
    runner = get_runner()
    intent = {
        "message": "recover me",
        "sender_session_id": "p1",
        "sender_msg_id": "a1",
        "sender_agent_id": "main",
        "agent_id": "main",
        "chain_messages": 0,
        "chain_generations": 0,
        "target_head_id": "a1",
        "task_id": "t_crash_deferred",
        "tracked_task": False,
    }

    with pytest.raises(SystemExit):
        runner.spawn_task(
            task_id="t_crash_deferred",
            session_id="p1",
            prompt="[message from p1:a1] recover me",
            agent_id="main",
            parent_msg_id="a1",
            caller_msg_id="a1",
            caller_session_id="p1",
            creates_agent=False,
            defer_dispatch=True,
            deferred_inbox=intent,
            on_accepted=lambda _task: (_ for _ in ()).throw(SystemExit()),
        )
    assert inbox.pending_count("p1") == 0

    governor = runner._governor
    runner_mod.shutdown_runner()
    recovered = runner_mod.TaskRunner(max_workers=1, governor=governor)
    try:
        assert inbox.pending_count("p1") == 1
    finally:
        recovered.shutdown()


def test_sync_child_inside_governed_worker_borrows_parent_live_claim(
    store_fixture, monkeypatch,
):
    """A same-session sync child completes with one worker/live slot."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger
    ledger = UsageLedger(store_fixture.root_path.parent / "borrow.db")
    runner = TaskRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    calls: list[str] = []

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        calls.append(prompt)
        if prompt == "parent":
            child = run_agent_turn(
                session_id=session_id,
                prompt="child",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(
                head_id="parent_head",
                final_text=f"parent saw {child.final_text}",
                failed=child.failed,
                error=child.error,
            )
        return AgentTurnResult(head_id="child_head", final_text="child done")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent", agent_id="main", parent_msg_id="a1",
    )
    try:
        final = runner.await_task(parent_id, timeout=2.0)
        assert final is not None
        assert final.status == TaskStatus.COMPLETED
        assert final.result_text == "parent saw child done"
        assert calls == ["parent", "child"]
    finally:
        for task in runner.list_tasks("p1"):
            if task.status not in {
                TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERRORED,
            }:
                runner.cancel_task(task.id, reason="test cleanup")
        runner.shutdown(wait=False)


def test_borrowed_child_system_exit_finalizes_and_cleans_child_ownership(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    class FatalProcess:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    class FatalRuntime:
        def __init__(self, process):
            self._proc = process

    ledger = UsageLedger(tmp_path / "borrow-system-exit.db")
    runner = TaskRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    process = FatalProcess()
    observed: dict = {}

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        if prompt == "parent-fatal":
            try:
                run_agent_turn(
                    session_id=session_id,
                    prompt="child-fatal",
                    agent_id=agent_id,
                    branch_from="a1",
                    caller_msg_id="a1",
                    chain_generations=1,
                )
            except SystemExit as exc:
                observed["exception"] = exc
                child_id = observed["child_id"]
                observed["child"] = runner.get_task(child_id)
                observed["child_admission"] = ledger.connection().execute(
                    "SELECT state, reason_code FROM task_admissions "
                    "WHERE task_id = ?", (child_id,),
                ).fetchone()
                observed["parent_admission"] = ledger.connection().execute(
                    "SELECT state, owner_instance_id, lease_generation "
                    "FROM task_admissions WHERE task_id = ?",
                    (observed["parent_id"],),
                ).fetchone()
                observed["child_token"] = run_control.current_token(
                    session_id, execution_id=child_id,
                )
                observed["current_execution_id"] = (
                    run_control.get_current_execution_id()
                )
                with runner._lock:
                    observed["child_in_tasks"] = child_id in runner._tasks
                    observed["child_in_done"] = child_id in runner._done_events
                observed["lease_alive"] = any(
                    thread.name == f"op-task-borrowed-lease-{child_id}"
                    and thread.is_alive()
                    for thread in threading.enumerate()
                )
                run_control.kill_active_runtime(
                    session_id, execution_id=child_id,
                )
            return AgentTurnResult(
                head_id="parent-head", final_text="parent recovered",
            )
        child_id = run_control.get_current_execution_id()
        observed["child_id"] = child_id
        run_control.register_active_runtime(
            session_id, FatalRuntime(process), execution_id=child_id,
        )
        raise SystemExit("child fatal")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent-fatal", agent_id="main",
        parent_msg_id="a1",
    )
    observed["parent_id"] = parent_id
    try:
        parent = runner.await_task(parent_id, timeout=2.0)

        assert parent is not None
        assert parent.status == TaskStatus.COMPLETED
        assert isinstance(observed["exception"], SystemExit)
        assert str(observed["exception"]) == "child fatal"
        child = observed["child"]
        assert child.status == TaskStatus.ERRORED
        assert child.reason_code == "error.execution"
        assert child.error == "SystemExit: child fatal"
        assert tuple(observed["child_admission"]) == (
            "released", "error.execution",
        )
        parent_admission = observed["parent_admission"]
        assert parent_admission[0] == "live"
        assert parent_admission[1] == runner._instance_id
        assert parent_admission[2] > 0
        assert observed["child_token"] is None
        assert observed["current_execution_id"] == parent_id
        assert observed["child_in_tasks"] is False
        assert observed["child_in_done"] is False
        assert observed["lease_alive"] is False
        assert process.terminated is False
    finally:
        child_id = observed.get("child_id")
        if child_id is not None:
            run_control.unregister_active_runtime(
                "p1", execution_id=child_id,
            )
            token = run_control.current_token("p1", execution_id=child_id)
            if token is not None:
                run_control.unregister_cancel_event(
                    "p1", token.event, execution_id=child_id,
                )
        runner.shutdown(wait=False)


def test_borrowed_child_runtime_budget_cancels_child_and_cleans_runtime(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    child_entered = threading.Event()
    release_child = threading.Event()
    child_ids: list[str] = []

    def limits(_session_id, task):
        seconds = 1 if task.prompt == "child-runtime" else 10
        return resolve_resource_limits(
            ResourceLimits(max_runtime_seconds=seconds), scheduler_capacity=1,
        )

    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-runtime.db"), limit_resolver=limits,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        if prompt == "parent-runtime":
            child = run_agent_turn(
                session_id=session_id,
                prompt="child-runtime",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(
                head_id="parent-head", final_text=child.error or "child ended",
            )
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        while not release_child.wait(0.01):
            if run_control.is_cancelled(session_id, execution_id=child_id):
                return AgentTurnResult(failed=True, error="stopped")
        return AgentTurnResult(head_id="child-head", final_text="released")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent-runtime", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        runtime_row = runner._governor.ledger.connection().execute(
            "SELECT state, owner_instance_id, lease_generation, started_at, "
            "lease_expires_at FROM task_admissions WHERE task_id = ?",
            (child_id,),
        ).fetchone()
        assert runtime_row[0] == "queued"
        assert runtime_row[1] == runner._instance_id
        assert runtime_row[2] > 0
        assert runtime_row[3] is not None
        assert runtime_row[4] is not None
        with runner._lock:
            assert runner._tasks[child_id]["time_limits"] == (1, None)
            assert runner._tasks[child_id]["lease_generation"] == runtime_row[2]
        live_count = runner._governor.ledger.connection().execute(
            "SELECT COUNT(*) FROM task_admissions "
            "WHERE state IN ('live','stopping')",
        ).fetchone()[0]
        assert live_count == 1
        clock.advance(1.1)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            child = runner.get_task(child_id)
            if child is not None and child.status == TaskStatus.CANCELLED:
                break
            time.sleep(0.01)
        child = runner.get_task(child_id)
        assert child is not None
        assert child.status == TaskStatus.CANCELLED
        assert child.reason_code == "budget.runtime_exhausted"
        assert runner.await_task(parent_id, timeout=2.0).status == TaskStatus.COMPLETED
        with runner._lock:
            assert child_id not in runner._tasks
            assert child_id not in runner._done_events
        assert run_control.current_token("p1", execution_id=child_id) is None
        row = runner._governor.ledger.connection().execute(
            "SELECT state FROM task_admissions WHERE task_id = ?", (child_id,),
        ).fetchone()
        assert row[0] == "released"
    finally:
        release_child.set()
        runner.shutdown(wait=False)


def test_borrowed_child_activity_refreshes_child_and_parent_idle(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    child_entered = threading.Event()
    record_activity = threading.Event()
    activity_recorded = threading.Event()
    release_child = threading.Event()
    child_ids: list[str] = []
    activity_results: list[bool] = []
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-idle.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        if prompt == "parent-idle":
            run_agent_turn(
                session_id=session_id,
                prompt="child-idle",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        while not release_child.wait(0.01):
            if record_activity.is_set() and not activity_recorded.is_set():
                from openprogram.agent.task.runner import (
                    record_current_task_activity,
                )
                activity_results.append(
                    record_current_task_activity("provider_data")
                )
                activity_recorded.set()
        return AgentTurnResult(head_id="child-head", final_text="child done")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent-idle", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        clock.advance(0.75)
        record_activity.set()
        assert activity_recorded.wait(1.0)
        assert activity_results == [True]
        activity_rows = runner._governor.ledger.connection().execute(
            "SELECT task_id, last_activity_at FROM task_admissions "
            "WHERE task_id IN (?, ?)",
            (child_id, parent_id),
        ).fetchall()
        assert len(activity_rows) == 2
        assert activity_rows[0][1] == activity_rows[1][1]
        clock.advance(0.5)
        time.sleep(0.05)
        assert runner.get_task(child_id).status == TaskStatus.RUNNING
        assert runner.get_task(parent_id).status == TaskStatus.RUNNING
        with runner._lock:
            assert runner._tasks[child_id]["last_activity_monotonic"] == 0.75
            assert runner._tasks[parent_id]["last_activity_monotonic"] == 0.75
        release_child.set()
        assert runner.await_task(parent_id, timeout=2.0).status == TaskStatus.COMPLETED
    finally:
        release_child.set()
        runner.shutdown(wait=False)


def test_borrowed_child_timeout_uses_child_and_ancestor_remaining_budget(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import (
        TaskRunner, current_task_operation_timeout,
    )
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    observed: list[float | None] = []

    def limits(_session_id, task):
        seconds = 8 if task.prompt == "child-timeout" else 10
        return resolve_resource_limits(
            ResourceLimits(max_runtime_seconds=seconds), scheduler_capacity=1,
        )

    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-timeout.db"), limit_resolver=limits,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        if prompt == "parent-timeout":
            clock.advance(7.0)
            run_agent_turn(
                session_id=session_id,
                prompt="child-timeout",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        observed.append(current_task_operation_timeout(None))
        clock.advance(2.0)
        observed.append(current_task_operation_timeout(5.0))
        return AgentTurnResult(head_id="child-head", final_text="done")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent-timeout", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        runner.await_task(parent_id, timeout=2.0)
        assert observed == [3.0, 1.0]
    finally:
        runner.shutdown(wait=False)


def test_explicit_cancel_of_borrowed_child_is_task_keyed_and_cleans_up(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    child_entered = threading.Event()
    release_child = threading.Event()
    child_ids: list[str] = []
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(UsageLedger(tmp_path / "borrow-cancel.db")),
    )
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)

    def fake_execute(*, session_id, prompt, agent_id, **_kwargs):
        if prompt == "parent-cancel":
            run_agent_turn(
                session_id=session_id,
                prompt="child-cancel",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        release_child.wait(2.0)
        return AgentTurnResult(head_id="child-head", final_text="late")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", fake_execute,
    )
    parent_id = runner.spawn_task(
        session_id="p1", prompt="parent-cancel", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        child_token = run_control.current_token("p1", execution_id=child_id)
        parent_token = run_control.current_token("p1", execution_id=parent_id)
        assert child_token is not None
        assert parent_token is not None
        runner.cancel_task(child_id, reason="cancel child only")
        assert child_token.event.is_set()
        assert not parent_token.event.is_set()
        release_child.set()
        assert runner.await_task(parent_id, timeout=2.0).status == TaskStatus.COMPLETED
        child = runner.get_task(child_id)
        assert child.status == TaskStatus.CANCELLED
        assert child.reason_code == "cancel.user"
        with runner._lock:
            assert child_id not in runner._tasks
            assert child_id not in runner._done_events
        assert run_control.current_token("p1", execution_id=child_id) is None
    finally:
        release_child.set()
        runner.shutdown(wait=False)


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


def test_runner_dispatch_submit_failure_terminalizes_published_task(
    store_fixture, monkeypatch, tmp_path,
):
    """A claimed task must not outlive a failed executor submission."""
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _task: resolved,
        ),
    )

    def reject_submission(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(runner._pool, "submit", reject_submission)
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="dispatch me", agent_id="main",
        )
        deadline = time.time() + 2.0
        admission = None
        while time.time() < deadline:
            admission = ledger.connection().execute(
                "SELECT state, reason_code FROM task_admissions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if admission is not None and admission[0] == "released":
                break
            time.sleep(0.01)

        assert admission is not None
        assert tuple(admission) == ("released", "error.dispatch_failed")
        final = runner.await_task(task_id, timeout=0.1)
        assert final is not None
        assert final.status == TaskStatus.ERRORED
        assert final.reason_code == "error.dispatch_failed"
    finally:
        runner.shutdown()


def test_runner_executes_three_live_tasks_for_one_session(
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

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=3), scheduler_capacity=3,
    )
    runner = TaskRunner(
        max_workers=3,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        task_ids = [
            runner.spawn_task(
                session_id="p1", prompt=f"same-session-{index}", agent_id="main",
            )
            for index in range(3)
        ]
        deadline = time.time() + 2
        while len(fake_worker[0]) < 3 and time.time() < deadline:
            time.sleep(0.01)

        assert len(fake_worker[0]) == 3
        assert ledger.connection().execute(
            "SELECT COUNT(*) FROM task_admissions WHERE state = 'live'"
        ).fetchone()[0] == 3
        from openprogram.agent import run_control
        assert all(
            run_control.current_token("p1", execution_id=task_id) is not None
            for task_id in task_ids
        )

        fake_worker[1].set()
        assert all(
            runner.await_task(task_id, timeout=5).status.value == "completed"
            for task_id in task_ids
        )
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_cancel_one_same_session_task_does_not_cancel_sibling(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=2), scheduler_capacity=2,
    )
    runner = TaskRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        first = runner.spawn_task(
            session_id="p1", prompt="cancel-first", agent_id="main",
        )
        second = runner.spawn_task(
            session_id="p1", prompt="keep-second", agent_id="main",
        )
        deadline = time.time() + 2
        while len(fake_worker[0]) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(fake_worker[0]) == 2

        runner.cancel_task(first)
        assert runner.await_task(first, timeout=5).status.value == "cancelled"
        sibling_token = run_control.current_token("p1", execution_id=second)
        assert sibling_token is not None
        assert not sibling_token.event.is_set()
        assert runner.get_task(second).status.value == "running"

        fake_worker[1].set()
        assert runner.await_task(second, timeout=5).status.value == "completed"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_idle_activity_is_tracked_per_same_session_task(
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
        ResourceLimits(
            max_live_per_session=2,
            max_runtime_seconds=10,
            idle_timeout_seconds=1,
        ),
        scheduler_capacity=2,
    )
    runner = TaskRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        active = runner.spawn_task(
            session_id="p1", prompt="active", agent_id="main",
        )
        idle = runner.spawn_task(
            session_id="p1", prompt="idle", agent_id="main",
        )
        deadline = time.time() + 2
        while len(fake_worker[0]) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(fake_worker[0]) == 2

        clock.advance(0.75)
        assert runner.record_task_activity(active, "provider_data")
        clock.advance(0.5)

        idle_final = runner.await_task(idle, timeout=5)
        assert idle_final.status.value == "cancelled"
        assert idle_final.reason_code == "budget.idle_exhausted"
        assert runner.get_task(active).status.value == "running"

        fake_worker[1].set()
        assert runner.await_task(active, timeout=5).status.value == "completed"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_runner_task_coexists_with_mcp_foreground_token(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    store_fixture.create_session("p2", "main", title="other")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    mcp_event = threading.Event()
    assert run_control.claim_cancel_event("p1", mcp_event)
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="wait for MCP", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        row = ledger.connection().execute(
            "SELECT state, owner_instance_id FROM task_admissions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        assert row[0] == "live"
        assert row[1] is not None
        assert [call["session_id"] for call in fake_worker[0]] == ["p1"]
        assert run_control.current_token("p1").event is mcp_event
        task_token = run_control.current_token("p1", execution_id=task_id)
        assert task_token is not None
        assert task_token.event is not mcp_event

        fake_worker[1].set()
        assert runner.await_task(task_id, timeout=5).status.value == "completed"
    finally:
        run_control.unregister_cancel_event("p1", mcp_event)
        fake_worker[1].set()
        runner.shutdown()


def test_runner_releases_stopping_claim_when_cancel_races_busy_requeue(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _task: resolved,
    )
    original_requeue = governor.requeue_task

    def cancel_before_requeue(task_id, **fence):
        governor.request_stop(task_id, "cancel.user")
        return original_requeue(task_id, **fence)

    monkeypatch.setattr(governor, "requeue_task", cancel_before_requeue)
    runner = TaskRunner(max_workers=1, governor=governor)
    mcp_event = threading.Event()
    task_id = "task_cancel_race"
    assert run_control.claim_cancel_event(
        "p1", mcp_event, execution_id=task_id,
    )
    try:
        runner.spawn_task(
            task_id=task_id, session_id="p1",
            prompt="cancel race", agent_id="main",
        )
        deadline = time.time() + 2
        row = None
        while time.time() < deadline:
            row = ledger.connection().execute(
                "SELECT state, reason_code FROM task_admissions WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is not None and row[0] == "released":
                break
            time.sleep(0.01)

        assert tuple(row) == ("released", "cancel.user")
        assert runner.get_task(task_id).status == TaskStatus.CANCELLED
        assert fake_worker[0] == []
        assert run_control.current_token(
            "p1", execution_id=task_id,
        ).event is mcp_event
    finally:
        run_control.unregister_cancel_event(
            "p1", mcp_event, execution_id=task_id,
        )
        fake_worker[1].set()
        runner.shutdown()


def test_runner_recovers_failed_stopping_finalize_and_continues_dispatch(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )
    store_fixture.create_session("p2", "main", title="other")
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    import openprogram.agent.task.runner as runner_mod
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=2)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _task: resolved,
    )
    original_requeue = governor.requeue_task
    original_store_update = runner_mod._store_update_status

    def cancel_before_requeue(task_id, **fence):
        governor.request_stop(task_id, "cancel.user")
        return original_requeue(task_id, **fence)

    def fail_cancel_store(session_id, task_id, status, **fields):
        if status == TaskStatus.CANCELLED:
            raise OSError("task store unavailable")
        return original_store_update(session_id, task_id, status, **fields)

    monkeypatch.setattr(governor, "requeue_task", cancel_before_requeue)
    monkeypatch.setattr(runner_mod, "_store_update_status", fail_cancel_store)
    runner = TaskRunner(max_workers=1, governor=governor)
    mcp_event = threading.Event()
    failed_id = "task_failed_requeue"
    assert run_control.claim_cancel_event(
        "p1", mcp_event, execution_id=failed_id,
    )
    try:
        runner.spawn_task(
            task_id=failed_id, session_id="p1",
            prompt="cancel race", agent_id="main",
        )
        other_id = runner.spawn_task(
            session_id="p2", prompt="still dispatch", agent_id="main",
        )

        assert fake_worker[3].wait(2)
        row = ledger.connection().execute(
            "SELECT state, owner_instance_id, lease_expires_at, lease_generation "
            "FROM task_admissions WHERE task_id = ?",
            (failed_id,),
        ).fetchone()
        assert row[0] == "stopping"
        assert row[1] == runner._instance_id
        assert row[2] is not None
        assert row[3] == 1
        assert ledger.connection().execute(
            "SELECT state FROM task_finalizations WHERE task_id = ?",
            (failed_id,),
        ).fetchone()[0] == "pending"
        assert [call["session_id"] for call in fake_worker[0]] == ["p2"]

        monkeypatch.setattr(
            runner_mod, "_store_update_status", original_store_update,
        )
        runner._reconcile_resources()
        assert runner.get_task(failed_id).status == TaskStatus.CANCELLED
        assert tuple(ledger.connection().execute(
            "SELECT state, reason_code FROM task_admissions WHERE task_id = ?",
            (failed_id,),
        ).fetchone()) == ("released", "cancel.user")

        fake_worker[1].set()
        assert runner.await_task(other_id, timeout=5).status == TaskStatus.COMPLETED
    finally:
        run_control.unregister_cancel_event(
            "p1", mcp_event, execution_id=failed_id,
        )
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


def test_cancel_watchdog_does_not_finalize_while_worker_is_still_running(
    monkeypatch,
):
    from openprogram.agent.task import runner as runner_module
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import Task, TaskStatus

    task = Task(id="live", parent_session_id="s1", prompt="p", agent_id="a")
    task.status = TaskStatus.RUNNING
    finalized = []

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(runner_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module, "_store_load", lambda _sid, _tid: task)
    runner = object.__new__(TaskRunner)
    runner._finalize_task_status = lambda *_args, **_kwargs: finalized.append(True)

    runner._schedule_force_cancel("s1", task.id, 1)

    assert finalized == []
    assert task.status == TaskStatus.RUNNING


def test_runner_recognizes_its_lock_holding_instance(
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
        "openprogram.agent.sub_agent_run._execute_agent_turn", stubborn_run,
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
        assert runner.operation_timeout_reason(task_id, None) == (
            "budget.idle_exhausted"
        )
        from openprogram.agent.task.runner import NonPreemptibleOperation
        with pytest.raises(NonPreemptibleOperation) as caught:
            runner.bounded_operation_timeout(
                task_id, 1.0, preemptibility="none",
            )
        assert caught.value.reason_code == "error.nonpreemptible_operation"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_nonpreemptible_operation_keeps_stable_task_terminal_reason(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None,
    )

    def failed_run(**_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        return AgentTurnResult(
            failed=True,
            error=(
                "NonPreemptibleOperation: "
                "error.nonpreemptible_operation"
            ),
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", failed_run,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.task.runner import TaskRunner
    from openprogram.agent.task.types import TaskStatus
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10), scheduler_capacity=1,
    )
    runner = TaskRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _task: resolved,
        ),
    )
    try:
        task_id = runner.spawn_task(
            session_id="p1", prompt="unsafe", agent_id="main",
        )
        final = runner.await_task(task_id, timeout=5)
        assert final.status == TaskStatus.ERRORED
        assert final.reason_code == "error.nonpreemptible_operation"
    finally:
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
