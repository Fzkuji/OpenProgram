from __future__ import annotations

import threading

from tests.component.agent.async_job_support import fake_worker, store_fixture

def test_public_spawn_creates_job_id_bound_canonical_execution(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.execution import default_store as default_execution_store
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="canonical job", agent_id="main",
        )
        execution = default_execution_store().get_execution(job_id)
        assert execution is not None
        assert execution.execution_id == job_id
        assert execution.status.value in {"queued", "running", "completed"}
        assert execution.capabilities.to_dict() == {
            "pause": False,
            "step": False,
            "steer": False,
            "fork": False,
            "retry": False,
            "safe_point_kinds": [],
            "state_schema_version": None,
        }
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_public_cancel_persists_canonical_execution_command(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.execution import (
        CommandKind,
        CommandStatus,
        default_store as default_execution_store,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cancel me", agent_id="main",
        )
        assert fake_worker[3].wait(3)
        runner.cancel_job(job_id)
        commands = default_execution_store().list_commands(job_id)
        cancel = [item for item in commands if item.kind is CommandKind.CANCEL]
        assert cancel
        assert cancel[-1].status in {
            CommandStatus.APPLYING,
            CommandStatus.APPLIED,
        }
        fake_worker[1].set()
    finally:
        runner.shutdown()


def test_transport_neutral_cancel_projects_and_releases_queued_job(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.agent import run_control
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import default_store as default_execution_store
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module, "_runner", runner)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="queued cancel", agent_id="main",
            defer_dispatch=True,
        )
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelled"
        assert runner.get_job(job_id).status.value == "cancelled"
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "released"
        assert [item.kind.value for item in
                default_execution_store().list_commands(job_id)] == [
                    "execution.cancel",
                ]
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_terminal_projection_retry_releases_after_store_failure(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.agent import run_control
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import default_store as default_execution_store
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module, "_runner", runner)
    original_update = runner_module._store_update_status
    fail_projection = True

    def fail_cancel_projection(session_id, job_id, status, **fields):
        if fail_projection and status is JobStatus.CANCELLED:
            raise OSError("projection unavailable")
        return original_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(
        runner_module, "_store_update_status", fail_cancel_projection,
    )
    runner2 = None
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="retry projection", agent_id="main",
            defer_dispatch=True,
        )
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelled"
        assert runner.get_job(job_id).status == JobStatus.QUEUED
        assert tuple(ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()) == ("queued",)
        assert ledger.connection().execute(
            "SELECT state FROM job_finalizations WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "pending"
        assert [item.kind.value for item in
                default_execution_store().list_commands(job_id)] == [
                    "execution.cancel",
                ]

        fail_projection = False
        runner.shutdown()
        runner2 = JobRunner(
            max_workers=1,
            governor=ResourceGovernor(ledger),
        )
        assert runner2.get_job(job_id).status == JobStatus.CANCELLED
        assert tuple(ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()) == ("released", "cancel.user")
    finally:
        fake_worker[1].set()
        if runner2 is not None:
            runner2.shutdown()
        else:
            runner.shutdown()


def test_failed_termination_keeps_live_owner_until_exact_recovery(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    """A failed terminate callback cannot publish a terminal projection."""
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.sub_agent_run import AgentTurnResult
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import TerminationReceipt
    from openprogram.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    monkeypatch.setattr(runner_module, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(runner_module, "_CANCEL_ESCALATION_SECS", 0.01)
    entered = threading.Event()
    release_worker = threading.Event()

    def hold_worker(**_kwargs):
        entered.set()
        release_worker.wait(10.0)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", hold_worker,
    )
    schedule_called = threading.Event()
    terminate_called = threading.Event()

    async def fail_terminate(**_kwargs):
        terminate_called.set()
        return TerminationReceipt(
            attempt_id=_kwargs["attempt_id"], terminated=False,
            reason=_kwargs["reason"],
        )

    ledger = UsageLedger(tmp_path / "termination-failure.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        real_schedule = runner._schedule_canonical_termination

        def schedule(*args, **kwargs):
            schedule_called.set()
            return real_schedule(*args, **kwargs)

        monkeypatch.setattr(runner, "_schedule_canonical_termination", schedule)
        monkeypatch.setattr(
            runner._execution_control, "terminate_attempt", fail_terminate,
        )
        job_id = runner.spawn_job(
            session_id="p1", prompt="terminate failure", agent_id="main",
        )
        assert entered.wait(2.0)
        runner.cancel_job(job_id)
        assert wait_until(
            lambda: runner._execution_store.get_execution(job_id).status.value
            == "cancelling",
            timeout=2.0,
        )
        assert schedule_called.wait(2.0)
        assert terminate_called.wait(2.0)
        admission = ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()
        assert runner.get_job(job_id).status.value == "running"
        assert admission[0] == "stopping"

        execution = runner._execution_store.get_execution(job_id)
        recovery = runner._execution_control.recover_owner_loss(
            job_id,
            attempt_id=execution.current_attempt_id,
            generation=execution.owner_lease["generation"],
        )
        runner._project_canonical_terminal(
            recovery.execution,
            admission_owner_instance_id=runner._instance_id,
            admission_lease_generation=runner._governor.admission_fence(job_id)[1],
        )
        assert runner.get_job(job_id).status.value in {"cancelled", "errored"}
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "released"
    finally:
        release_worker.set()
        runner.shutdown()


def test_cancel_projection_failure_returns_recovery_required_and_blocks_dispatch(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent import run_control
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import CommandStatus
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    monkeypatch.setattr(runner_module, "_broadcast", lambda *a, **k: None)
    original_update = runner_module._store_update_status

    def fail_cancel_projection(session_id, job_id, status, **fields):
        if status is JobStatus.CANCELLED:
            raise OSError("projection unavailable")
        return original_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(
        runner_module, "_store_update_status", fail_cancel_projection,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module, "_runner", runner)

    def fail_enqueue(*_args, **_kwargs):
        raise OSError("governance unavailable")

    monkeypatch.setattr(
        runner._governor, "enqueue_terminal_projection", fail_enqueue,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="projection failure", agent_id="main",
            defer_dispatch=True,
        )
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelled"
        assert result["issue_code"] == "projection_recovery_required"
        assert result["recovery_required"] is True
        assert runner.get_job(job_id).status == JobStatus.QUEUED
        command = runner._execution_store.get_command(
            f"execution-cancel:{job_id}",
        )
        assert command is not None
        assert command.status is CommandStatus.REJECTED
        assert command.rejection_code == "projection_recovery_required"
        admission = ledger.connection().execute(
            "SELECT state, dispatch_ready FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        assert tuple(admission) == ("queued", 0)
        assert runner._governor.claim_next(owner_instance_id=runner._instance_id) is None
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_force_termination_releases_exact_owner_after_background_escalation(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.sub_agent_run import AgentTurnResult
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import TerminationReceipt
    from openprogram.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    monkeypatch.setattr(runner_module, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(runner_module, "_CANCEL_ESCALATION_SECS", 0.01)
    entered = threading.Event()
    release_worker = threading.Event()
    schedule_called = threading.Event()
    terminate_called = threading.Event()

    def hold_worker(**_kwargs):
        entered.set()
        release_worker.wait(10.0)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", hold_worker,
    )

    async def terminate(**_kwargs):
        terminate_called.set()
        return TerminationReceipt(
            attempt_id=_kwargs["attempt_id"], terminated=True,
            reason=_kwargs["reason"],
        )

    ledger = UsageLedger(tmp_path / "termination-success.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        monkeypatch.setattr(
            runner._execution_control, "terminate_attempt", terminate,
        )
        real_schedule = runner._schedule_canonical_termination

        def schedule(*args, **kwargs):
            schedule_called.set()
            return real_schedule(*args, **kwargs)

        monkeypatch.setattr(runner, "_schedule_canonical_termination", schedule)
        job_id = runner.spawn_job(
            session_id="p1", prompt="force termination", agent_id="main",
        )
        assert entered.wait(2.0)
        runner.cancel_job(job_id)
        assert schedule_called.wait(2.0)
        assert terminate_called.wait(2.0)
        assert wait_until(
            lambda: ledger.connection().execute(
                "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
            ).fetchone()[0] == "released",
            timeout=2.0,
        )
        assert runner.get_job(job_id).status in {
            JobStatus.CANCELLED, JobStatus.ERRORED,
        }
        assert ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()[1] in {"cancel.user", "owner_lost"}
    finally:
        release_worker.set()
        runner.shutdown()
