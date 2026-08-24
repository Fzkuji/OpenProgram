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
    # sessions so the session-level cancel event for the queued job
    # doesn't bleed into the running one (cancel is session-scoped
    # per D5 of the design).
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
    res = runner.cancel_job(tid2)
    assert res is not None
    assert res.status in (JobStatus.CANCELLED, JobStatus.ERRORED)
    barrier.set()
    final = runner.await_job(tid1, timeout=5.0)
    assert final.status == JobStatus.COMPLETED

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
    # cancelling — otherwise cancel_job can flip the job to
    # cancelled while it's still pending, _run_one's
    # pending→running transition gets rejected, and fake_run never
    # gets a chance to observe the cancel signal.
    assert entered.wait(timeout=2.0), "fake worker never started"
    # Don't release barrier — cancel mid-run.
    runner.cancel_job(tid)
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status in (JobStatus.CANCELLED, JobStatus.ERRORED)
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
    statuses = [runner.get_job(t).status for t in ids]
    # First either pending/queued/running, later ones should not be running.
    running = [s for s in statuses if s == JobStatus.RUNNING]
    assert len(running) <= 1
    # Now drain.
    barrier.set()
    for t in ids:
        final = runner.await_job(t, timeout=5.0)
        assert final.status in (JobStatus.COMPLETED, JobStatus.ERRORED)

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
        assert terminal["data"]["resource"]["resource_state"] == "released"
    finally:
        runner.shutdown()

def test_running_job_binds_one_immutable_governance_context(
    store_fixture, monkeypatch, tmp_path,
):
    from dataclasses import is_dataclass

    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    captured = []

    def inspect_context(**_kwargs):
        from openprogram.agent.sub_agent_run import AgentTurnResult
        from openprogram.agent.job.runner import current_job_resource_context

        captured.append(current_job_resource_context())
        return AgentTurnResult(
            head_id="head", final_text="done", failed=False, error=None,
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", inspect_context,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.job.runner import (
        JobRunner,
        current_job_resource_context,
    )
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
        assert runner.await_job(job_id, timeout=5).status.value == "completed"
    finally:
        runner.shutdown()

    assert len(captured) == 1
    context = captured[0]
    assert context.job_id == job_id
    assert context.budget_scope_id
    assert context.governor is runner._governor
    assert context.ledger_identity == str(ledger._path().resolve())
    assert dict(context.effective_limits)["max_total_tokens"] == 10_000
    assert callable(context.deadline_callback)
    assert callable(context.activity_callback)
    assert is_dataclass(context)
    assert context.__dataclass_params__.frozen is True
    assert current_job_resource_context() is None

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
            runner.await_job(job_id, timeout=5).status.value == "completed"
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
        assert wait_until(lambda: len(fake_worker[0]) >= 2, timeout=2.0)
        assert len(fake_worker[0]) == 2

        runner.cancel_job(first)
        assert runner.await_job(first, timeout=5).status.value == "cancelled"
        sibling_token = run_control.current_token("p1", execution_id=second)
        assert sibling_token is not None
        assert not sibling_token.event.is_set()
        assert runner.get_job(second).status.value == "running"

        fake_worker[1].set()
        assert runner.await_job(second, timeout=5).status.value == "completed"
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
        budget_poll_seconds=0.01,
    )
    try:
        active = runner.spawn_job(
            session_id="p1", prompt="active", agent_id="main",
        )
        idle = runner.spawn_job(
            session_id="p1", prompt="idle", agent_id="main",
        )
        assert wait_until(lambda: len(fake_worker[0]) >= 2, timeout=2.0)
        assert len(fake_worker[0]) == 2

        clock.advance(0.75)
        assert runner.record_job_activity(active, "provider_data")
        clock.advance(0.5)
        assert wait_until(
            lambda: runner.get_job(idle).reason_code == "budget.idle_exhausted",
            timeout=2.0,
        )

        idle_final = runner.await_job(idle, timeout=5)
        assert idle_final.status.value == "cancelled"
        assert idle_final.reason_code == "budget.idle_exhausted"
        assert runner.get_job(active).status.value == "running"

        fake_worker[1].set()
        assert runner.await_job(active, timeout=5).status.value == "completed"
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
        assert runner.await_job(job_id, timeout=5).status.value == "completed"
    finally:
        run_control.unregister_cancel_event("p1", mcp_event)
        fake_worker[1].set()
        runner.shutdown()

def test_runner_releases_stopping_claim_when_cancel_races_busy_requeue(
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
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    original_requeue = governor.requeue_job

    def cancel_before_requeue(job_id, **fence):
        governor.request_stop(job_id, "cancel.user")
        return original_requeue(job_id, **fence)

    monkeypatch.setattr(governor, "requeue_job", cancel_before_requeue)
    runner = JobRunner(max_workers=1, governor=governor)
    mcp_event = threading.Event()
    job_id = "job_cancel_race"
    assert run_control.claim_cancel_event(
        "p1", mcp_event, execution_id=job_id,
    )
    try:
        runner.spawn_job(
            job_id=job_id, session_id="p1",
            prompt="cancel race", agent_id="main",
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
        row = admission_row()

        assert tuple(row) == ("released", "cancel.user")
        assert runner.get_job(job_id).status == JobStatus.CANCELLED
        assert fake_worker[0] == []
        assert run_control.current_token(
            "p1", execution_id=job_id,
        ).event is mcp_event
    finally:
        run_control.unregister_cancel_event(
            "p1", mcp_event, execution_id=job_id,
        )
        fake_worker[1].set()
        runner.shutdown()

def test_runner_recovers_failed_stopping_finalize_and_continues_dispatch(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    store_fixture.create_session("p2", "main", title="other")
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    import openprogram.agent.job.runner as runner_mod
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "governance.db")
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=2)
    governor = ResourceGovernor(
        ledger, limit_resolver=lambda _sid, _job: resolved,
    )
    original_requeue = governor.requeue_job
    original_store_update = runner_mod._store_update_status

    def cancel_before_requeue(job_id, **fence):
        governor.request_stop(job_id, "cancel.user")
        return original_requeue(job_id, **fence)

    def fail_cancel_store(session_id, job_id, status, **fields):
        if status == JobStatus.CANCELLED:
            raise OSError("job store unavailable")
        return original_store_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(governor, "requeue_job", cancel_before_requeue)
    monkeypatch.setattr(runner_mod, "_store_update_status", fail_cancel_store)
    runner = JobRunner(max_workers=1, governor=governor)
    mcp_event = threading.Event()
    failed_id = "job_failed_requeue"
    assert run_control.claim_cancel_event(
        "p1", mcp_event, execution_id=failed_id,
    )
    try:
        runner.spawn_job(
            job_id=failed_id, session_id="p1",
            prompt="cancel race", agent_id="main",
        )
        other_id = runner.spawn_job(
            session_id="p2", prompt="still dispatch", agent_id="main",
        )

        assert fake_worker[3].wait(2)
        row = ledger.connection().execute(
            "SELECT state, owner_instance_id, lease_expires_at, lease_generation "
            "FROM job_admissions WHERE job_id = ?",
            (failed_id,),
        ).fetchone()
        assert row[0] == "stopping"
        assert row[1] == runner._instance_id
        assert row[2] is not None
        assert row[3] == 1
        assert ledger.connection().execute(
            "SELECT state FROM job_finalizations WHERE job_id = ?",
            (failed_id,),
        ).fetchone()[0] == "pending"
        assert [call["session_id"] for call in fake_worker[0]] == ["p2"]

        monkeypatch.setattr(
            runner_mod, "_store_update_status", original_store_update,
        )
        runner._reconcile_resources()
        assert runner.get_job(failed_id).status == JobStatus.CANCELLED
        assert tuple(ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (failed_id,),
        ).fetchone()) == ("released", "cancel.user")

        fake_worker[1].set()
        assert runner.await_job(other_id, timeout=5).status == JobStatus.COMPLETED
    finally:
        run_control.unregister_cancel_event(
            "p1", mcp_event, execution_id=failed_id,
        )
        fake_worker[1].set()
        runner.shutdown()
