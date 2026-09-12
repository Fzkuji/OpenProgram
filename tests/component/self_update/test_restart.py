"""Update maintenance uses canonical pause ownership and durable queues."""

from types import SimpleNamespace

from tests.component.agent.async_job_support import store_fixture, fake_worker  # noqa: F401


def test_queued_jobs_do_not_block_update(monkeypatch):
    from openprogram.self_update import supervisor
    from openprogram.agent.job import store as jobs
    from openprogram.agent.job.types import JobStatus
    from openprogram import store as sessions

    class Sessions:
        def list_sessions(self, *, status=None, **kwargs):
            return [] if status else [{"id": "queued-session"}]

    monkeypatch.setattr(sessions, "default_store", Sessions)
    monkeypatch.setattr(
        jobs,
        "list_jobs",
        lambda *args, status_filter, **kw: (
            [SimpleNamespace(status=JobStatus.QUEUED)]
            if JobStatus.QUEUED in status_filter
            else []
        ),
    )
    monkeypatch.setattr(supervisor.time, "sleep", lambda _: None)
    assert supervisor._wait_for_quiescence(supervisor.time.time() + 1)


def _environment(tmp_path, monkeypatch):
    from openprogram.execution import (
        ExecutionStore,
        AttemptStore,
        RuntimeControlService,
    )
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.model import CapabilitySet
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from tests.unit.self_update.test_store import _request
    from openprogram import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "test"})
    execution = executions.create_execution(
        execution_id="active",
        run_id="run",
        session_id="session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True, safe_point_kinds=("action.after",), state_schema_version=1
        ),
    )
    attempts = AttemptStore(executions)
    leased, execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker",
        ttl_seconds=300,
    )
    attempt, execution = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=execution.status_version,
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    monkeypatch.setattr(
        "openprogram.execution.control.default_control_service", lambda: service
    )
    runner = SimpleNamespace(_execution_store=executions, _execution_control=service)
    updates = SelfUpdateStore()
    updates.create(_request())
    updates.transition("su_test", UpdatePhase.STAGING)
    updates.transition("su_test", UpdatePhase.READY)
    return updates, runner, service, attempt


def test_update_pause_survives_restart_and_continues_once(tmp_path, monkeypatch):
    from openprogram.self_update import restart, UpdatePhase
    from openprogram.self_update.maintenance import enter_maintenance, leave_maintenance
    from openprogram.execution.checkpoints import CheckpointFragment
    from openprogram.execution import RuntimeControlService, AttemptStore
    from openprogram.execution.driver import DriverRegistry

    updates, runner, service, attempt = _environment(tmp_path, monkeypatch)
    enter_maintenance("su_test")
    restart.reconcile(runner)
    current = runner._execution_store.get_execution("active")
    assert current.status.value == "pausing"
    pause_id = restart._command_id("su_test", "active", "pause")
    paused = service.arrive_safe_point(
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        command_id=pause_id,
        expected_execution_version=current.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "next", "phase": "before"},),
            state_refs={"cursor": 1},
        ),
    ).execution
    assert paused.checkpoint_head_id
    restart.reconcile(runner)
    assert runner._execution_store.get_execution("active").status.value == "paused"
    updates.transition("su_test", UpdatePhase.ABORTED)
    # Terminal state alone does not authorize task activation.
    restart.reconcile(runner)
    assert runner._execution_store.get_execution("active").status.value == "paused"
    leave_maintenance("su_test")
    fresh = RuntimeControlService(
        runner._execution_store, AttemptStore(runner._execution_store), DriverRegistry()
    )
    monkeypatch.setattr(
        "openprogram.execution.control.default_control_service", lambda: fresh
    )
    restart.reconcile(runner)
    resumed = runner._execution_store.get_execution("active")
    assert resumed.status.value == "running"
    assert resumed.checkpoint_head_id == paused.checkpoint_head_id
    restart.reconcile(runner)
    assert (
        runner._execution_store.get_execution("active").current_attempt_id
        == resumed.current_attempt_id
    )
    assert len(runner._execution_store.list_commands("active")) == 2


def test_user_pause_is_not_claimed_by_update(tmp_path, monkeypatch):
    import asyncio
    from openprogram.self_update import restart, UpdatePhase
    from openprogram.self_update.maintenance import enter_maintenance, leave_maintenance

    updates, runner, service, attempt = _environment(tmp_path, monkeypatch)
    current = runner._execution_store.get_execution("active")
    asyncio.run(
        service.request_pause(
            command_id="user-pause",
            execution_id="active",
            expected_version=current.status_version,
            actor={"surface": "user"},
        )
    )
    enter_maintenance("su_test")
    restart.reconcile(runner)
    updates.transition("su_test", UpdatePhase.ABORTED)
    leave_maintenance("su_test")
    restart.reconcile(runner)
    assert [c.command_id for c in runner._execution_store.list_commands("active")] == [
        "user-pause"
    ]


def test_maintenance_keeps_job_queued_until_release(
    tmp_path, monkeypatch, store_fixture, fake_worker
):
    from openprogram.self_update.maintenance import enter_maintenance, leave_maintenance
    from openprogram.self_update import SelfUpdateStore, UpdatePhase
    from openprogram.agent.job.runner import JobRunner
    from openprogram import paths
    from tests.unit.self_update.test_store import _request

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    updates = SelfUpdateStore()
    updates.create(_request())
    updates.transition("su_test", UpdatePhase.STAGING)
    updates.transition("su_test", UpdatePhase.READY)
    enter_maintenance("su_test")
    runner = JobRunner(max_workers=1)
    try:
        job_id = runner.spawn_job("p1", "preserve queued work", "main")
        from openprogram.self_update.maintenance import claim_job

        assert (
            claim_job(
                runner._governor,
                owner_instance_id=runner._instance_id,
                excluded_sessions=set(),
                only_job_id=job_id,
            )
            is None
        )
        assert runner._execution_store.get_execution(job_id).status.value == "queued"
        assert not runner._governor.has_live_jobs()
        updates.transition("su_test", UpdatePhase.ABORTED)
        leave_maintenance("su_test")
        runner._dispatch_wake.set()
        assert fake_worker[3].wait(5)
        assert fake_worker[0][0]["prompt"] == "preserve queued work"
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_supervisor_refreshes_session_status_written_by_worker(tmp_path, monkeypatch):
    from openprogram.store import SessionStore
    from openprogram.self_update import supervisor

    stale = SessionStore(tmp_path / "sessions")
    try:
        stale.create_session("legacy", "main")
        stale.update_session("legacy", status="running")
        stale._flush_index()
        worker = SessionStore(stale.root_path)
        try:
            worker.update_session("legacy", status="idle")
        finally:
            worker.close()
        assert stale.list_sessions(status="running")
        monkeypatch.setattr("openprogram.store.default_store", lambda: stale)
        assert supervisor._wait_for_quiescence(supervisor.time.time() + 1)
    finally:
        stale.close()


def test_quiescence_preserves_inactive_effect_reconciliation(tmp_path, monkeypatch):
    from openprogram.self_update import supervisor
    from openprogram.execution.model import ExecutionStatus
    from openprogram.agent.resource_governance import ResourceGovernor

    _, runner, service, attempt = _environment(tmp_path, monkeypatch)
    executions = runner._execution_store
    monkeypatch.setattr("openprogram.execution.store.default_store", lambda: executions)
    monkeypatch.setattr(
        "openprogram.store.default_store",
        lambda: SimpleNamespace(list_sessions=lambda **_: []),
    )
    # An owned attempt still prevents installation.
    assert not supervisor._wait_for_quiescence(supervisor.time.time() + 0.01)
    before = executions.get_execution("active")
    service.attempts.finish(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=before.status_version,
        target=ExecutionStatus.RECONCILIATION_REQUIRED,
        outcome="reconciliation_required",
        reason_code="effect_reconciliation",
    )
    pending = executions.get_execution("active")
    assert pending.current_attempt_id is None
    assert supervisor._wait_for_quiescence(supervisor.time.time() + 0.01)
    assert executions.get_execution("active") == pending
    # Claims are checked independently of the execution projection.
    monkeypatch.setattr(ResourceGovernor, "has_live_jobs", lambda _: True)
    assert not supervisor._wait_for_quiescence(supervisor.time.time() + 0.01)
    assert executions.get_execution("active") == pending
