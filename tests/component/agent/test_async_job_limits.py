"""Async Job limits component tests."""
from __future__ import annotations

import threading
import time

import pytest

from tests.component.agent.async_job_support import (
    _FakeMonotonic,
    WORKER_START_TIMEOUT,
    fake_worker,
    store_fixture,
)
from tests.support.waiting import wait_until


def test_queued_cancel_does_not_cancel_unrelated_session_runtime(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
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
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        runner.spawn_job(session_id="p1", prompt="live", agent_id="main")
        queued = runner.spawn_job(
            session_id="p2", prompt="queued", agent_id="main",
        )
        assert fake_worker[3].wait(WORKER_START_TIMEOUT)

        runner.cancel_job(queued)

        assert cancelled_sessions == []
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runner_restart_dispatches_persisted_governed_queue(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.store import save_job
    from openprogram.agent.job.types import Job, JobStatus
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "governance.db"),
        limit_resolver=lambda _sid, _job: resolved,
    )
    job = Job(
        id="restart_queued", parent_session_id="p1",
        prompt="resume me", agent_id="main",
    )
    governor.admit_job(job, persist=lambda accepted: save_job("p1", accepted))

    runner = JobRunner(max_workers=1, governor=governor)
    try:
        assert fake_worker[3].wait(WORKER_START_TIMEOUT)
        fake_worker[1].set()
        assert runner.await_job(job.id, timeout=5).status == JobStatus.COMPLETED
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_worker_lost_fence_prevents_stale_runner_from_writing_completed(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    runner = JobRunner(max_workers=1, governor=governor)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="stale", agent_id="main",
        )
        assert fake_worker[3].wait(WORKER_START_TIMEOUT)
        ledger.connection().execute(
            "UPDATE job_admissions SET lease_expires_at = 0 WHERE job_id = ?",
            (job_id,),
        )
        ledger.connection().commit()

        reconciled = governor.reconcile(
            job_lookup=lambda session_id, current_job_id: load_job(
                session_id, current_job_id,
            ),
            mark_worker_lost=runner._mark_worker_lost,
            owner_is_alive=lambda _owner: False,
            now=1,
        )
        fake_worker[1].set()
        final = runner.await_job(job_id, timeout=5)

        assert reconciled.released_worker_lost == 1
        assert final.status == JobStatus.ERRORED
        assert final.reason_code == "error.worker_lost"
        assert final.result_text in (None, "")
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_cancel_watchdog_does_not_finalize_while_worker_is_still_running(
    monkeypatch,
):
    from openprogram.agent.job import runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import Job, JobStatus

    job = Job(id="live", parent_session_id="s1", prompt="p", agent_id="a")
    job.status = JobStatus.RUNNING
    finalized = []

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(runner_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner_module, "_store_load", lambda _sid, _tid: job)
    runner = object.__new__(JobRunner)
    runner._finalize_job_status = lambda *_args, **_kwargs: finalized.append(True)

    runner._schedule_force_cancel("s1", job.id, 1)

    assert finalized == []
    assert job.status == JobStatus.RUNNING

def test_runner_recognizes_its_lock_holding_instance(
    store_fixture, fake_worker, tmp_path,
):
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        assert runner._owner_holds_worker_lock(runner._instance_id) is True
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runtime_budget_moves_live_job_to_stopping_until_worker_exits(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
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
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=1), scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="stubborn", agent_id="main",
        )
        assert entered.wait(WORKER_START_TIMEOUT)
        clock.advance(1.1)
        def admission_state():
            return ledger.connection().execute(
                "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
            ).fetchone()[0]

        assert wait_until(lambda: admission_state() == "stopping")
        state = admission_state()
        assert state == "stopping"
        assert runner.get_job(job_id).status == JobStatus.RUNNING
        release.set()
        final = runner.await_job(job_id, timeout=5)
        assert final.status == JobStatus.CANCELLED
        assert final.reason_code == "budget.runtime_exhausted"
    finally:
        release.set()
        runner.shutdown()

def test_idle_budget_resets_only_after_meaningful_activity(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    budget_polled = threading.Event()

    def observed_clock():
        budget_polled.set()
        return clock()

    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
        monotonic_clock=observed_clock,
        budget_poll_seconds=0.01,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="idle", agent_id="main",
        )
        assert fake_worker[3].wait(WORKER_START_TIMEOUT)
        clock.advance(0.75)
        assert runner.record_job_activity(job_id, "transport_keepalive") is False
        assert runner.record_job_activity(job_id, "provider_data") is True
        budget_polled.clear()
        clock.advance(0.75)
        assert budget_polled.wait(1.0)
        assert runner.get_job(job_id).status == JobStatus.RUNNING
        budget_polled.clear()
        clock.advance(0.30)
        assert budget_polled.wait(1.0)
        assert wait_until(
            lambda: runner.get_job(job_id).reason_code == "budget.idle_exhausted",
            timeout=2.0,
        )
        final = runner.await_job(job_id, timeout=5)
        assert final.status == JobStatus.CANCELLED
        assert final.reason_code == "budget.idle_exhausted"
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_bounded_operation_timeout_clamps_and_rejects_unbounded_strict_work(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=4),
        scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="bounded", agent_id="main",
        )
        assert fake_worker[3].wait(WORKER_START_TIMEOUT)

        assert runner.bounded_operation_timeout(job_id, 8.0) == 4.0
        clock.advance(3.0)
        assert runner.bounded_operation_timeout(job_id, 8.0) == 1.0
        assert runner.bounded_operation_timeout(job_id, None) == 1.0
        assert runner.operation_timeout_reason(job_id, None) == (
            "budget.idle_exhausted"
        )
        from openprogram.agent.job.runner import NonPreemptibleOperation
        with pytest.raises(NonPreemptibleOperation) as caught:
            runner.bounded_operation_timeout(
                job_id, 1.0, preemptibility="none",
            )
        assert caught.value.reason_code == "error.nonpreemptible_operation"
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_nonpreemptible_operation_keeps_stable_job_terminal_reason(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
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
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10), scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="unsafe", agent_id="main",
        )
        final = runner.await_job(job_id, timeout=5)
        assert final.status == JobStatus.ERRORED
        assert final.reason_code == "error.nonpreemptible_operation"
    finally:
        runner.shutdown()

def test_runner_releases_bookkeeping_after_completion(store_fixture, fake_worker,
                                                      monkeypatch):
    """Both _jobs AND _done_events must be emptied once a job ends.

    _done_events used to be written on spawn and never popped, leaking
    one threading.Event per job for the process lifetime. Popping it
    is safe because await_job grabs its reference before waiting.
    """
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    _, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    barrier.set()  # let workers run straight through
    ids = [
        runner.spawn_job(
            session_id="p1", prompt=f"n{i}", agent_id="main",
            parent_msg_id="a1",
        )
        for i in range(5)
    ]
    for t in ids:
        assert runner.await_job(t, timeout=5.0).status == JobStatus.COMPLETED
    assert wait_until(lambda: not runner._jobs and not runner._done_events)
    assert runner._jobs == {}, "job entries leaked"
    assert runner._done_events == {}, "done-events leaked"

def test_runner_await_after_completion_still_returns(store_fixture, fake_worker,
                                                     monkeypatch):
    """await_job on an already-finished job works with no done-event.

    Once _done_events is popped, a late waiter falls through to the
    terminal-status check and returns immediately.
    """
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    _, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    barrier.set()
    tid = runner.spawn_job(
        session_id="p1", prompt="quick", agent_id="main", parent_msg_id="a1",
    )
    assert runner.await_job(tid, timeout=5.0).status == JobStatus.COMPLETED
    # Second await, long after the event was dropped, must not hang.
    started = time.time()
    again = runner.await_job(tid, timeout=5.0)
    assert again.status == JobStatus.COMPLETED
    assert time.time() - started < 1.0
