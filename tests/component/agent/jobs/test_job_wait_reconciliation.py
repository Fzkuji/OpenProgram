"""JobRunner owns live durable-wait expiry and event publication."""
from __future__ import annotations

import asyncio
import threading
import time

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.execution.store import ExecutionStore
from openprogram.execution.waits import DurableWaitStore, WaitStatus


def _active_execution(tmp_path):
    executions = ExecutionStore(tmp_path / "execution.sqlite3")
    revision = executions.create_revision(manifest={"entrypoint": "job"})
    execution = executions.create_execution(
        execution_id="job-wait-expiry",
        run_id="job-wait-run",
        session_id="job-session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("agent.provider.decision.after",),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="job-worker",
        ttl_seconds=30,
        attempt_id="job-attempt",
    )
    attempt, execution = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, attempts, execution, attempt


def test_job_reconciler_expires_unanswered_wait_and_applies_timeout_policy(
    tmp_path,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.agent.job.runner import JobRunner

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider:decision"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="ask",
        request={"prompt": "Continue?"},
        policy_snapshot={
            "version": 1,
            "on_answer": "continue",
            "on_decline": "fail",
            "on_timeout": "fail",
        },
        expires_at=time.time() + 60,
        wait_id="job-expiring-wait",
    )
    with executions._transaction() as connection:
        connection.execute(
            "UPDATE execution_waits SET expires_at = 1 WHERE wait_id = ?",
            (suspended.wait.wait_id,),
        )

    runner = JobRunner.__new__(JobRunner)
    runner._execution_store = executions
    runner._execution_waits = DurableWaitStore(executions)
    runner._execution_control = service
    runner._dispatch_wake = threading.Event()

    runner._reconcile_execution_waits()

    wait = runner._execution_waits.get_wait(suspended.wait.wait_id)
    assert wait is not None and wait.status is WaitStatus.EXPIRED
    updated = executions.get_execution(execution.execution_id)
    assert updated is not None
    assert updated.status is ExecutionStatus.FAILED
    assert updated.reason_code == "wait_timeout"


def test_default_job_driver_publishes_question_events(monkeypatch):
    from openprogram.agent.job import runner as runner_module

    sent: list[dict] = []
    monkeypatch.setattr(runner_module.shared, "_broadcast", sent.append)
    runner = runner_module.JobRunner.__new__(runner_module.JobRunner)
    runner._agent_driver_factory = None
    runner._execution_store = object()
    runner._execution_control = object()
    runner._governor = type(
        "Governor",
        (),
        {"continuation_parent_msg_id": staticmethod(lambda _job_id: None)},
    )()

    driver = runner._agent_driver()
    assert driver.event_sink is runner_module.shared._broadcast

    event = {"type": "question.asked", "data": {"execution_id": "job-1"}}
    driver.event_sink(event)
    assert sent == [event]


def test_job_recovery_leaves_conversation_wait_to_its_control_service(tmp_path):
    from openprogram.agent.job.runner import JobRunner

    executions, _, execution, _ = _active_execution(tmp_path)
    runner = JobRunner.__new__(JobRunner)
    runner._execution_store = executions
    # This execution has no Job admission. Recovery must not try to queue it
    # through the Job resource scheduler or prevent worker startup.
    runner._queue_wait_resume(None, execution)
    assert executions.get_execution(execution.execution_id) == execution


def test_job_recovery_delegates_conversation_wait_to_canonical_control_service(
    tmp_path, monkeypatch,
):
    from openprogram.agent.job.runner import JobRunner

    executions, _, execution, _ = _active_execution(tmp_path)
    resumed = []

    class _CanonicalControl:
        def __init__(self, store):
            self.executions = store

        async def _resume_wait_if_required(self, *, wait, execution):
            resumed.append((wait, execution.execution_id))
            return execution

    canonical = _CanonicalControl(executions)
    monkeypatch.setattr(
        "openprogram.execution.control.default_control_service",
        lambda: canonical,
    )
    runner = JobRunner.__new__(JobRunner)
    runner._execution_store = executions
    runner._execution_control = object()

    result = runner._queue_wait_resume(None, execution)
    assert result is not None
    asyncio.run(result)
    assert resumed == [(None, execution.execution_id)]


def test_resolved_system_wait_reaches_canonical_continuation_after_job_reconcile(
    tmp_path, monkeypatch,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.agent.job.runner import JobRunner

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "system-access"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="system_access",
        request={"required_capabilities": ["screen_recording"]},
        policy_snapshot={"version": 1, "kind": "system_access", "on_grant": "continue"},
        expires_at=0,
        wait_id="job-system-access-resume",
    )
    wait = DurableWaitStore(executions)
    wait.resolve_system_access(
        suspended.wait.wait_id,
        report={"capabilities": [{"id": "screen_recording", "status": "granted"}]},
        owner_id="worker-test",
    )
    activations = []

    async def activate(attempt, activation):
        activations.append((attempt.attempt_id, activation.checkpoint.checkpoint_id))

    canonical = RuntimeControlService(
        executions, attempts, DriverRegistry(), activator=activate,
    )
    job_control = RuntimeControlService(executions, attempts, DriverRegistry())

    monkeypatch.setattr(
        "openprogram.execution.control.default_control_service",
        lambda: canonical,
    )
    runner = JobRunner.__new__(JobRunner)
    runner._execution_store = executions
    runner._execution_control = job_control
    job_control.set_wait_resume_scheduler(runner._queue_wait_resume)

    asyncio.run(job_control.recover_wait_outcomes())

    resumed = executions.get_execution(execution.execution_id)
    assert resumed is not None
    assert resumed.status is ExecutionStatus.RUNNING
    assert resumed.current_attempt_id is not None
    assert resumed.current_attempt_id != attempt.attempt_id
    assert activations == [(
        resumed.current_attempt_id,
        suspended.checkpoint.checkpoint_id,
    )]

    # The resolved wait remains in the durable outcome log, but the canonical
    # continuation is idempotent once the same execution owns a new attempt.
    asyncio.run(job_control.recover_wait_outcomes())
    again = executions.get_execution(execution.execution_id)
    assert again is not None
    assert again.current_attempt_id == resumed.current_attempt_id
    assert activations == [(
        resumed.current_attempt_id,
        suspended.checkpoint.checkpoint_id,
    )]


def test_reconciler_schedules_recovery_on_an_existing_event_loop():
    from openprogram.agent.job.runner import JobRunner

    recovered = asyncio.Event()

    class _Waits:
        def reclaim_expired_claims(self):
            return 0

        def reclaim_orphaned_claims(self):
            return 0

        def expire_due(self):
            return 0

    class _Control:
        async def recover_wait_outcomes(self):
            recovered.set()
            return ("execution",)

    runner = JobRunner.__new__(JobRunner)
    runner._execution_waits = _Waits()
    runner._execution_control = _Control()
    runner._dispatch_wake = threading.Event()

    async def _run():
        runner._reconcile_execution_waits()
        await asyncio.wait_for(recovered.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert runner._wait_recovery_tasks == set()
    assert runner._dispatch_wake.is_set()


def test_startup_recovery_schedules_waits_on_an_existing_event_loop():
    from openprogram.execution import startup

    recovered = asyncio.Event()

    class _Control:
        async def recover_wait_outcomes(self):
            recovered.set()
            return ()

    async def _run():
        assert startup._recover_wait_outcomes(_Control()) is None
        await asyncio.wait_for(recovered.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert startup._PENDING_WAIT_RECOVERY_TASKS == set()
