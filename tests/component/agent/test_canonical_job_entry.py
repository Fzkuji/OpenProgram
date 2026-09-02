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


def test_public_cross_process_cancel_is_consumed_by_owner_worker(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    """A caller without the worker registry still reaches the owner worker."""
    from openprogram.agent import run_control
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import CommandStatus, default_store as default_execution_store
    from openprogram.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    import openprogram.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cross-process cancel", agent_id="main",
        )
        assert fake_worker[3].wait(3)

        # The public caller has no process-local JobRunner/DriverRegistry.
        monkeypatch.setattr(runner_module, "_runner", None)
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert fake_worker[2].wait(2)
        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=3,
        )
        execution = default_execution_store().get_execution(job_id)
        assert execution is not None
        assert execution.status.value == "cancelled"
        command = default_execution_store().get_command(
            f"execution-cancel:{job_id}",
        )
        assert command is not None
        assert command.status is CommandStatus.APPLIED
        assert wait_until(
            lambda: tuple(ledger.connection().execute(
                "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()) == ("released", "cancel.user"),
            timeout=3,
        )
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_public_job_reconciler_recovers_cancel_command_after_apply_failure(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent import run_control
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import CommandStatus, default_store as default_execution_store
    from openprogram.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    import openprogram.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    original_transition = runner._execution_store._transition_command
    fail_apply = True
    job_id: str | None = None

    def fail_first_terminal_cancel_apply(connection, command_id, **kwargs):
        nonlocal fail_apply
        terminal = (
            connection.execute(
                "SELECT status FROM executions WHERE execution_id = ?", (job_id,),
            ).fetchone()
            if job_id is not None else None
        )
        if (
            fail_apply
            and command_id == f"execution-cancel:{job_id}"
            and kwargs.get("target") is CommandStatus.APPLIED
            and terminal is not None
            and terminal[0] == "cancelled"
        ):
            fail_apply = False
            raise RuntimeError("injected cancel apply failure")
        return original_transition(connection, command_id, **kwargs)

    monkeypatch.setattr(
        runner._execution_store, "_transition_command", fail_first_terminal_cancel_apply,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="reconcile cancel command", agent_id="main",
        )
        assert fake_worker[3].wait(3)
        monkeypatch.setattr(runner_module, "_runner", None)
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert fake_worker[2].wait(2)

        assert wait_until(
            lambda: runner.get_job(job_id) is not None
            and runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=8,
        )
        command = default_execution_store().get_command(
            f"execution-cancel:{job_id}",
        )
        assert command is not None and command.status is CommandStatus.APPLIED
        assert runner._execution_control._delivered_cancel_commands == set()
        assert runner._execution_control._cancel_delivery_by_execution == {}
        assert tuple(ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (job_id,),
        ).fetchone()) == ("released", "cancel.user")
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_cross_process_cancel_wins_natural_completion_race(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.agent import run_control
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution import CommandStatus, default_store as default_execution_store
    from openprogram.usage.ledger import UsageLedger
    from openprogram.agent.sub_agent_run import AgentTurnResult
    from tests.support.waiting import wait_until

    entered = threading.Event()
    release_result = threading.Event()
    finish_started = threading.Event()
    allow_finish = threading.Event()

    def natural_worker(**_kwargs):
        entered.set()
        assert release_result.wait(3)
        return AgentTurnResult(
            final_text="natural result", head_id="natural-head",
        )

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn", natural_worker,
    )
    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    import openprogram.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        budget_poll_seconds=60,
    )
    original_finish = runner._finish_canonical_attempt

    def delayed_finish(*args, **kwargs):
        finish_started.set()
        assert allow_finish.wait(3)
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(runner, "_finish_canonical_attempt", delayed_finish)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="natural completion race", agent_id="main",
        )
        assert entered.wait(3)
        release_result.set()
        assert finish_started.wait(3)

        # The public caller has no process-local JobRunner/DriverRegistry.
        monkeypatch.setattr(runner_module, "_runner", None)
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        allow_finish.set()

        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=3,
        )
        execution = default_execution_store().get_execution(job_id)
        command = default_execution_store().get_command(
            f"execution-cancel:{job_id}",
        )
        assert execution is not None and execution.status.value == "cancelled"
        assert command is not None and command.status is CommandStatus.APPLIED
        assert execution.reason_code == "cancel.user"
        assert runner.get_job(job_id).reason_code == "cancel.user"
        assert wait_until(
            lambda: tuple(ledger.connection().execute(
                "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()) == ("released", "cancel.user"),
            timeout=3,
        )
    finally:
        allow_finish.set()
        runner.shutdown()


def test_cross_process_cancel_escalates_to_exact_owner_termination(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.agent import run_control
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult
    from openprogram.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    entered = threading.Event()
    release_worker = threading.Event()
    terminated = threading.Event()

    def uncooperative_worker(**_kwargs):
        entered.set()
        assert release_worker.wait(10)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run._execute_agent_turn",
        uncooperative_worker,
    )
    monkeypatch.setattr(runner_module, "_CANCEL_ESCALATION_SECS", 0.01)

    def terminate_process(*_args, **_kwargs):
        terminated.set()
        return True

    monkeypatch.setattr(
        "openprogram.agent.process_runner.kill_active_subprocess",
        terminate_process,
    )
    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        budget_poll_seconds=0.01,
    )
    import openprogram.execution.control as control_module
    control_module._default_control_services.clear()
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="force cross-process cancel", agent_id="main",
        )
        assert entered.wait(3)
        monkeypatch.setattr(runner_module, "_runner", None)
        result = run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert terminated.wait(3)
        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=3,
        )
        execution = runner._execution_store.get_execution(job_id)
        assert execution is not None and execution.status.value == "cancelled"
        assert execution.reason_code == "cancel.user"
        assert wait_until(
            lambda: tuple(ledger.connection().execute(
                "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()) == ("released", "cancel.user"),
            timeout=3,
        )
    finally:
        release_worker.set()
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
    claim_result: list[object] = []
    claim_done = threading.Event()

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
        def claim_during_recovery():
            claim_result.append(
                runner._governor.claim_next(owner_instance_id=runner._instance_id),
            )
            claim_done.set()

        claim_thread = threading.Thread(target=claim_during_recovery)
        claim_thread.start()
        assert claim_done.wait(2.0)
        claim_thread.join(timeout=1.0)
        raise OSError("governance unavailable")

    monkeypatch.setattr(
        runner._governor, "enqueue_terminal_projection", fail_enqueue,
    )
    try:
        runner.spawn_job(
            session_id="p1", prompt="occupy worker", agent_id="main",
        )
        assert fake_worker[3].wait(2.0)
        job_id = runner.spawn_job(
            session_id="p1", prompt="projection failure", agent_id="main",
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
        assert claim_result == [None]
        assert runner._governor.claim_next(owner_instance_id=runner._instance_id) is None
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_cancel_conflict_records_exact_barrier_and_recovers_deferred_dispatch(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.agent.job.runner as runner_module
    from openprogram.agent import run_control
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.execution.store import ExecutionConflict
    from openprogram.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.paths.get_execution_db_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module, "_runner", runner)
    original_accept = runner._execution_control.executions.accept_command_with_transition

    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cancel conflict", agent_id="main",
            defer_dispatch=True,
        )

        def conflict(**_kwargs):
            row = ledger.connection().execute(
                "SELECT state, dispatch_ready, terminal_blocked, "
                "terminal_block_command_id, terminal_block_phase "
                "FROM job_admissions WHERE job_id = ?", (job_id,),
            ).fetchone()
            assert tuple(row) == (
                "queued", 0, 1, f"execution-cancel:{job_id}", "prepared",
            )
            raise ExecutionConflict("stale_version", "canonical changed")

        monkeypatch.setattr(
            runner._execution_control.executions,
            "accept_command_with_transition", conflict,
        )
        result = run_control.cancel_execution(job_id)
        assert result["recovery_required"] is True
        barrier = ledger.connection().execute(
            "SELECT dispatch_ready, terminal_blocked, terminal_block_command_id, "
            "terminal_block_phase, terminal_block_prior_dispatch_ready "
            "FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()
        assert tuple(barrier) == (
            0, 1, f"execution-cancel:{job_id}", "recovery", 0,
        )

        monkeypatch.setattr(
            runner._execution_control.executions,
            "accept_command_with_transition", original_accept,
        )
        runner._reconcile_terminal_dispatch_barriers()
        assert tuple(ledger.connection().execute(
            "SELECT dispatch_ready, terminal_blocked FROM job_admissions "
            "WHERE job_id = ?", (job_id,),
        ).fetchone()) == (0, 0)
        assert runner._governor.claim_next(
            owner_instance_id=runner._instance_id,
        ) is None
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

    def terminate_process(*_args, **_kwargs):
        terminate_called.set()
        return True

    monkeypatch.setattr(
        "openprogram.agent.process_runner.kill_active_subprocess",
        terminate_process,
    )

    ledger = UsageLedger(tmp_path / "termination-success.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
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
