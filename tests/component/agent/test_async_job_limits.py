"""Async Job limits component tests."""
from __future__ import annotations

import asyncio
import threading

import pytest

from tests.component.agent.async_job_support import (
    _FakeMonotonic,
    fake_worker,
    store_fixture,
)
from tests.support.waiting import wait_until


def _await_canonical_terminal(runner, execution_id, *, timeout=5.0):
    execution = None

    def terminal():
        nonlocal execution
        execution = runner._execution_store.get_execution(execution_id)
        return execution is not None and execution.status.value in {
            "completed", "cancelled", "failed", "interrupted",
        }

    assert wait_until(terminal, timeout=timeout)
    assert execution is not None
    return execution


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

        runner.cancel_execution(queued)

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

def test_resource_saga_records_canonical_admission_before_dispatch(
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

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "governance.db"),
        limit_resolver=lambda _sid, _job: resolved,
    )
    runner = JobRunner(max_workers=1, governor=governor)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="resume me", agent_id="main",
        )
        assert fake_worker[3].wait(2.0)
        intents = runner._execution_store.list_resource_intents(
            execution_id=job_id,
        )
        assert {
            (intent["kind"], intent["state"])
            for intent in intents
        } >= {
            ("execution.admission.intent", "applied"),
            ("resource.admission.intent", "applied"),
        }
        fake_worker[1].set()
        assert _await_canonical_terminal(runner, job_id).status.value == "completed"
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


def test_canonical_driver_termination_returns_exact_receipt(monkeypatch, tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution import AttemptStore, ExecutionStore

    class Questions:
        def __init__(self):
            self.cancelled = []

        def cancel_execution(self, session_id, execution_id):
            self.cancelled.append((session_id, execution_id))

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(
        revision_id="revision-1", manifest={"entrypoint": "agent"},
    )
    execution = store.admit_execution(
        execution_id="job-1",
        run_id="run-1",
        session_id="session-1",
        revision_id=revision.revision_id,
        input_ref="input:job-1",
        input_hash="input-hash-1",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test"},
        config_snapshot_ref="config:job-1",
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "terminate",
                "agent_id": "main",
                "source": "test",
            },
        },
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker",
        ttl_seconds=30,
        attempt_id="attempt-1",
    )
    active, _running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    questions = Questions()
    monkeypatch.setattr(
        "openprogram.agent.process_runner.kill_active_subprocess",
        lambda session_id, *, execution_id: (
            session_id == "session-1" and execution_id == "job-1"
        ),
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.kill_active_runtime",
        lambda *_args, **_kwargs: None,
    )
    driver = AgentProductionDriver(store, question_registry=questions)
    binding = asyncio.run(driver.activate(active, None))
    receipt = asyncio.run(
        driver.terminate(binding.handle, "budget.runtime_exhausted"),
    )
    driver.activation_aborted(binding)
    assert receipt.attempt_id == "attempt-1"
    assert receipt.terminated is True
    assert questions.cancelled == [("session-1", "job-1")]

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
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(stubborn_run),
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
        execution = runner._execution_store.get_execution(job_id)
        assert execution is not None and execution.status.value == "cancelling"
        release.set()
        final = _await_canonical_terminal(runner, job_id)
        assert final.status.value == "cancelled"
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
        execution = runner._execution_store.get_execution(job_id)
        assert execution is not None and execution.status.value == "running"
        budget_polled.clear()
        clock.advance(0.30)
        assert budget_polled.wait(1.0)
        final = _await_canonical_terminal(runner, job_id)
        assert final.status.value == "cancelled"
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
