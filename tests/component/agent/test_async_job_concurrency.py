"""Async Job concurrency component tests."""
from __future__ import annotations

import threading

import pytest

from tests.component.agent.async_job_support import (
    _FakeMonotonic,
    fake_worker,
    store_fixture,
)
from tests.support.waiting import wait_until


def _canonical_execution(runner, execution_id):
    execution = runner._execution_store.get_execution(execution_id)
    assert execution is not None
    return execution


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


def test_runner_rejection_creates_no_job(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        AdmissionRejected,
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_jobs_per_session=1), scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        runner.spawn_job(session_id="p1", prompt="one", agent_id="main")
        with pytest.raises(AdmissionRejected) as caught:
            runner.spawn_job(session_id="p1", prompt="two", agent_id="main")
        assert caught.value.decision.reason_code == "quota.jobs_exhausted"
        assert [job.prompt for job in runner.list_jobs("p1")] == ["one"]
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runner_cancel_before_pickup(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    # Force a single-worker pool occupied by another job to keep the
    # second one queued; cancel the queued one. Use two different
    # sessions so the exact execution cancellation for the queued job
    # remains independent from the running execution.
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()
    calls, barrier, _, _ = fake_worker

    # Second session for the queued+cancelled job.
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

    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid1 = runner.spawn_job(
        session_id="p1", prompt="block", agent_id="main",
        parent_msg_id="a1",
    )
    assert fake_worker[3].wait(1.0)
    tid2 = runner.spawn_job(
        session_id="p2", prompt="cancel me", agent_id="main",
        parent_msg_id="a2",
    )
    # tid1 occupies the worker (waiting on barrier). tid2 sits in
    # queued. Cancel tid2 before it gets picked up.
    res = runner.cancel_execution(tid2)
    assert res is not None
    assert res.status in (JobStatus.CANCELLED, JobStatus.ERRORED)
    barrier.set()
    assert _await_canonical_terminal(runner, tid1).status.value == "completed"

def test_runner_cancel_during_run(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, cancel_seen, entered = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="will be cancelled", agent_id="main",
        parent_msg_id="a1",
    )
    # Wait until the worker is actually executing fake_run before
    # cancelling — otherwise cancel_execution can flip the job to
    # cancelled while it's still pending, _run_one's
    # pending→running transition gets rejected, and fake_run never
    # gets a chance to observe the cancel signal.
    assert entered.wait(timeout=2.0), "fake worker never started"
    # Don't release barrier — cancel mid-run.
    runner.cancel_execution(tid)
    final = _await_canonical_terminal(runner, tid)
    assert final.status.value == "cancelled"
    # Worker observed cancel (via is_cancelled flag).
    assert cancel_seen.is_set()

def test_runner_pool_backpressure(store_fixture, fake_worker, monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    monkeypatch.setenv("OPENPROGRAM_JOB_WORKERS", "1")
    import openprogram.agent.job.runner as runner_mod
    runner_mod.shutdown_runner()

    calls, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    ids = [
        runner.spawn_job(
            session_id="p1", prompt=f"n{i}", agent_id="main",
            parent_msg_id="a1",
        )
        for i in range(3)
    ]
    # Single worker occupied; others queued.
    assert fake_worker[3].wait(1.0)
    statuses = [_canonical_execution(runner, t).status.value for t in ids]
    # First either pending/queued/running, later ones should not be running.
    running = [status for status in statuses if status == "running"]
    assert len(running) <= 1
    # Now drain.
    barrier.set()
    for t in ids:
        assert _await_canonical_terminal(runner, t).status.value == "completed"

def test_runner_durable_dispatcher_skips_saturated_session(
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

    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=1), scheduler_capacity=2,
    )
    runner = JobRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        runner.spawn_job(session_id="p1", prompt="p1 first", agent_id="main")
        runner.spawn_job(session_id="p1", prompt="p1 second", agent_id="main")
        runner.spawn_job(session_id="p2", prompt="p2 first", agent_id="main")

        assert wait_until(lambda: len(fake_worker[0]) >= 2)
        assert {call["session_id"] for call in fake_worker[0]} == {"p1", "p2"}
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runner_dispatch_submit_failure_terminalizes_published_job(
    store_fixture, monkeypatch, tmp_path,
):
    """A claimed job must not outlive a failed executor submission."""
    broadcasts = []
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", broadcasts.append,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _job: resolved,
        ),
    )

    def reject_submission(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(runner._pool, "submit", reject_submission)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="dispatch me", agent_id="main",
        )
        def admission_row():
            return ledger.connection().execute(
                "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()

        assert wait_until(
            lambda: (
                (row := admission_row()) is not None
                and row[0] == "released"
            ),
            timeout=2.0,
        )
        admission = admission_row()

        assert admission is not None
        assert tuple(admission) == ("released", "error.dispatch_failed")
        final = runner.await_job(job_id, timeout=0.1)
        assert final is not None
        assert final.status == JobStatus.ERRORED
        assert final.reason_code == "error.dispatch_failed"
        def find_terminal():
            return next(
                (
                    event for event in reversed(broadcasts)
                    if event.get("type") == "job_status"
                    and event["data"]["status"] == "errored"
                ),
                None,
            )

        assert wait_until(lambda: find_terminal() is not None)
        terminal = find_terminal()
        assert terminal is not None
        assert "resource_state" not in terminal["data"]["resource"]
        assert terminal["data"]["resource"]["resource"]["resource_state"] == "released"
    finally:
        runner.shutdown()

def test_canonical_driver_receives_immutable_job_resource_context(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    captured = []

    def inspect_context(*, execution_context, **_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult

        captured.append(execution_context)
        return AgentTurnResult(
            head_id="head", final_text="done", failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(inspect_context),
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_total_tokens=10_000), scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="inspect", agent_id="main",
        )
        assert _await_canonical_terminal(runner, job_id).status.value == "completed"
    finally:
        runner.shutdown()

    assert len(captured) == 1
    context = captured[0]
    assert callable(context["safe_point_hook"])
    hints = context["job_context"]["resource_hints"]
    assert hints["admission_id"]
    assert hints["budget_scope_id"]
    assert hints["effective_limits"]["max_total_tokens"] == 10_000

def test_runner_executes_three_live_jobs_for_one_session(
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

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=3), scheduler_capacity=3,
    )
    runner = JobRunner(
        max_workers=3,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        job_ids = [
            runner.spawn_job(
                session_id="p1", prompt=f"same-session-{index}", agent_id="main",
            )
            for index in range(3)
        ]
        assert wait_until(lambda: len(fake_worker[0]) >= 3, timeout=2.0)
        assert len(fake_worker[0]) == 3
        assert ledger.connection().execute(
            "SELECT COUNT(*) FROM job_admissions WHERE state = 'live'"
        ).fetchone()[0] == 3
        from openprogram.agent import run_control
        assert all(
            run_control.current_token("p1", execution_id=job_id) is not None
            for job_id in job_ids
        )

        fake_worker[1].set()
        assert all(
            _await_canonical_terminal(runner, job_id).status.value == "completed"
            for job_id in job_ids
        )
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_cancel_one_same_session_job_does_not_cancel_sibling(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(
        ResourceLimits(max_live_per_session=2), scheduler_capacity=2,
    )
    runner = JobRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    try:
        first = runner.spawn_job(
            session_id="p1", prompt="cancel-first", agent_id="main",
        )
        second = runner.spawn_job(
            session_id="p1", prompt="keep-second", agent_id="main",
        )
        assert wait_until(lambda: len(fake_worker[0]) >= 2, timeout=10.0)
        assert len(fake_worker[0]) == 2

        runner.cancel_execution(first)
        assert _await_canonical_terminal(runner, first).status.value == "cancelled"
        sibling_token = run_control.current_token("p1", execution_id=second)
        assert sibling_token is not None
        assert not sibling_token.event.is_set()
        assert _canonical_execution(runner, second).status.value == "running"

        fake_worker[1].set()
        assert _await_canonical_terminal(runner, second).status.value == "completed"
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_idle_activity_is_tracked_per_same_session_job(
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
        ResourceLimits(
            max_live_per_session=2,
            max_runtime_seconds=10,
            idle_timeout_seconds=1,
        ),
        scheduler_capacity=2,
    )
    runner = JobRunner(
        max_workers=2,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "governance.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
        monotonic_clock=clock,
        # This test drives the monitor explicitly below. Keep the background
        # loop parked so it cannot claim the expiry between the fake-clock
        # advance and the deterministic tick.
        budget_poll_seconds=60,
    )
    try:
        active = runner.spawn_job(
            session_id="p1", prompt="active", agent_id="main",
        )
        idle = runner.spawn_job(
            session_id="p1", prompt="idle", agent_id="main",
        )
        assert wait_until(lambda: len(fake_worker[0]) >= 2, timeout=10.0)
        assert len(fake_worker[0]) == 2
        def monitor_ready():
            with runner._lock:
                entries = [runner._jobs.get(job_id, {}) for job_id in (active, idle)]
                return all(
                    entry.get("started_monotonic") is not None
                    and entry.get("attempt_id") is not None
                    for entry in entries
                )

        assert wait_until(monitor_ready, timeout=10.0)

        clock.advance(0.75)
        assert runner.record_job_activity(active, "provider_data")
        clock.advance(0.5)
        # Run the same monitor pass synchronously so CI scheduling load cannot
        # delay the expiry check beyond the terminal wait timeout.
        runner._budget_tick()
        with runner._lock:
            idle_cancel = runner._jobs[idle]["event"]
            active_cancel = runner._jobs[active]["event"]
        assert idle_cancel.is_set()
        assert not active_cancel.is_set()
        assert _canonical_execution(runner, active).status.value == "running"

        fake_worker[1].set()
        idle_final = _await_canonical_terminal(runner, idle, timeout=10.0)
        assert idle_final.status.value == "cancelled"
        assert idle_final.reason_code == "budget.idle_exhausted"
        assert _await_canonical_terminal(runner, active).status.value == "completed"
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_runner_job_coexists_with_mcp_foreground_token(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    store_fixture.create_session("p2", "main", title="other")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            ledger, limit_resolver=lambda _sid, _job: resolved,
        ),
    )
    mcp_event = threading.Event()
    assert run_control.claim_cancel_event("p1", mcp_event)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="wait for MCP", agent_id="main",
        )
        assert fake_worker[3].wait(1.0)
        row = ledger.connection().execute(
            "SELECT state, owner_instance_id FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        assert row[0] == "live"
        assert row[1] is not None
        assert [call["session_id"] for call in fake_worker[0]] == ["p1"]
        assert run_control.current_token("p1").event is mcp_event
        job_token = run_control.current_token("p1", execution_id=job_id)
        assert job_token is not None
        assert job_token.event is not mcp_event

        fake_worker[1].set()
        assert _await_canonical_terminal(runner, job_id).status.value == "completed"
    finally:
        run_control.unregister_cancel_event("p1", mcp_event)
        fake_worker[1].set()
        runner.shutdown()

def test_runner_releases_claim_when_canonical_cancel_races_activation(
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
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    runner = JobRunner(max_workers=1, governor=governor)
    activation_entered = threading.Event()
    allow_activation = threading.Event()
    original_activate = runner._activate_canonical_claim

    def delayed_activation(claim, cancel_event):
        activation_entered.set()
        assert allow_activation.wait(2.0)
        return original_activate(claim, cancel_event)

    monkeypatch.setattr(
        runner, "_activate_canonical_claim", delayed_activation,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1",
            prompt="cancel race", agent_id="main",
        )
        assert activation_entered.wait(2.0)
        runner.cancel_execution(job_id, reason="cancel.user")
        allow_activation.set()
        assert wait_until(
            lambda: (
                (row := ledger.connection().execute(
                    "SELECT state FROM job_admissions WHERE job_id = ?",
                    (job_id,),
                ).fetchone()) is not None
                and row[0] == "released"
            ),
            timeout=2.0,
        )
        row = ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        assert tuple(row) == ("released", "cancel.user")
        assert runner.get_job(job_id).status == JobStatus.CANCELLED
        assert fake_worker[0] == []
    finally:
        allow_activation.set()
        fake_worker[1].set()
        runner.shutdown()

def test_resource_saga_releases_terminal_canonical_execution(
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

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    runner = JobRunner(max_workers=1, governor=governor)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="finish projection", agent_id="main",
        )
        assert fake_worker[3].wait(2)
        fake_worker[1].set()
        assert _await_canonical_terminal(runner, job_id).status.value == "completed"
        assert wait_until(
            lambda: any(
                intent["kind"] == "resource.release.intent"
                and intent["state"] == "applied"
                for intent in runner._execution_store.list_resource_intents(
                    execution_id=job_id,
                )
            ),
            timeout=3.0,
        )
        assert tuple(ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()) == ("released", "completed")
    finally:
        fake_worker[1].set()
        runner.shutdown()
