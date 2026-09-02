"""Async Job limits component tests."""
from __future__ import annotations

import threading
import time

import pytest

from tests.component.agent.async_job_support import (
    _FakeMonotonic,
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
        assert fake_worker[3].wait(1.0)

        runner.cancel_job(queued)

        execution = runner._execution_store.get_execution(queued)
        assert execution is not None
        assert execution.status.value == "cancelled"
        assert runner._governor.ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (queued,),
        ).fetchone()[0] == "released"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_budget_reason_survives_cancel_intent_persistence_failure(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import resource_governance
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    resolved = resource_governance.resolve_resource_limits(
        resource_governance.ResourceLimits(max_runtime_seconds=1),
        scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=resource_governance.ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="persist-failure", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        clock.advance(1.1)
        final = runner.await_job(job_id, timeout=5.0)

        assert final.status == JobStatus.CANCELLED
        assert final.reason_code == "budget.runtime_exhausted"
        commands = runner._execution_store.list_commands(job_id)
        assert [c.kind.value for c in commands] == ["execution.cancel"]
        row = runner._governor.ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        assert tuple(row) == ("released", "budget.runtime_exhausted")
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
    runner = JobRunner(max_workers=1, governor=governor)
    runner._admit_canonical_job(job)
    governor.admit_job(job, persist=lambda accepted: save_job("p1", accepted))
    try:
        assert fake_worker[3].wait(2.0)
        fake_worker[1].set()
        assert runner.await_job(job.id, timeout=5).status == JobStatus.COMPLETED
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runner_startup_wakes_dispatcher_before_fallback_poll(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    """A persisted dispatch-ready job is claimed by the startup wake."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.store import save_job
    from openprogram.agent.job.types import Job
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "governance.db"),
        limit_resolver=lambda _sid, _job: resolved,
    )
    job = Job(
        id="startup_wake", parent_session_id="p1",
        prompt="start immediately", agent_id="main",
    )

    dispatch_gate = threading.Event()
    dispatcher_entered = threading.Event()
    captured_wake = {}
    real_dispatch_loop = JobRunner._dispatch_loop

    def blocked_dispatch_loop(runner):
        captured_wake["event"] = runner._dispatch_wake
        dispatcher_entered.set()
        dispatch_gate.wait(timeout=5.0)
        return real_dispatch_loop(runner)

    monkeypatch.setattr(JobRunner, "_dispatch_loop", blocked_dispatch_loop)
    runner = None
    try:
        runner = JobRunner(max_workers=1, governor=governor)
        runner._admit_canonical_job(job)
        governor.admit_job(job, persist=lambda accepted: save_job("p1", accepted))
        assert dispatcher_entered.wait(1.0)
        wake = captured_wake.get("event")
        assert wake is not None
        assert wake.is_set()
        dispatch_gate.set()
        assert fake_worker[3].wait(1.0)
    finally:
        dispatch_gate.set()
        fake_worker[1].set()
        if runner is not None:
            runner.shutdown()


def test_worker_lost_fence_recovers_exact_canonical_owner(
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

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    runner = JobRunner(max_workers=1, governor=governor)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="stale", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        ledger.connection().execute(
            "UPDATE job_admissions SET lease_expires_at = 0 WHERE job_id = ?",
            (job_id,),
        )
        ledger.connection().commit()
        runner._owner_holds_worker_lock = lambda _owner: False
        runner._reconcile_resources()
        fake_worker[1].set()
        final = runner.await_job(job_id, timeout=5)

        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "released"
        canonical = runner._execution_store.get_execution(job_id)
        assert canonical is not None
        assert canonical.status.value == "interrupted"
        assert final.status == JobStatus.ERRORED
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_canonical_driver_termination_returns_exact_receipt():
    from openprogram.agent.job.driver import JobDriver
    from openprogram.execution import AttemptRecord, AttemptStatus, TerminationReceipt

    terminated = []
    driver = JobDriver(
        execution_id="job-1",
        terminate_callback=lambda handle, reason: (
            terminated.append((handle.attempt_id, reason))
            or TerminationReceipt(handle.attempt_id, True, reason)
        ),
    )
    attempt = AttemptRecord(
        attempt_id="attempt-1", execution_id="job-1", generation=1,
        status=AttemptStatus.ACTIVE, owner_id="worker", lease_expires_at=9999999999,
        leased_at=1, updated_at=1, activated_at=1,
    )
    import asyncio
    handle = asyncio.run(driver.activate(attempt, None))
    driver.activation_committed(type("Binding", (), {
        "driver": driver, "execution_id": "job-1", "attempt_id": "attempt-1",
        "generation": 1, "handle": handle,
    })())
    receipt = asyncio.run(driver.terminate(handle, "budget.runtime_exhausted"))
    assert receipt.attempt_id == "attempt-1"
    assert receipt.terminated is True
    assert terminated == [("attempt-1", "budget.runtime_exhausted")]

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
        assert entered.wait(1.0)
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
        assert fake_worker[3].wait(1.0)
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
        assert fake_worker[3].wait(1.0)

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
