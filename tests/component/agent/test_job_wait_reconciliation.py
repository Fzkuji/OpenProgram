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
            "UPDATE execution_waits SET expires_at = 0 WHERE wait_id = ?",
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
    monkeypatch.setattr(runner_module, "_broadcast", sent.append)
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
    assert driver.event_sink is runner_module._broadcast

    event = {"type": "question.asked", "data": {"execution_id": "job-1"}}
    driver.event_sink(event)
    assert sent == [event]


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
