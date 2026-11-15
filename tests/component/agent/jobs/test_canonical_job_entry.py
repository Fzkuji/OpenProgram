from __future__ import annotations

from contextlib import contextmanager
import threading

from tests.component.agent import async_job_support as _async_job_support

# Shared pytest fixtures, bound at module level so pytest collects them here.
fake_worker = _async_job_support.fake_worker
store_fixture = _async_job_support.store_fixture


def _admission_columns(ledger, job_id, *columns):
    row = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
    return tuple(row[column] for column in columns)


def test_public_spawn_creates_job_id_bound_canonical_execution(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.runtime.execution import default_store as default_execution_store
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
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
            "pause": True,
            "step": True,
            "steer": True,
            "fork": True,
            "retry": True,
            "safe_point_kinds": [
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ],
            "state_schema_version": 1,
        }
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_driver_starts_after_running_projection_and_preserves_early_cancel(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger

    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path",
        lambda: tmp_path / "execution.sqlite3",
    )
    projection_entered = threading.Event()
    allow_projection = threading.Event()
    original_update = runner_module.shared._store_update_status

    def hold_running_projection(session_id, job_id, status, **fields):
        if status is JobStatus.RUNNING:
            projection_entered.set()
            assert allow_projection.wait(30)
        return original_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(
        runner_module.shared, "_store_update_status", hold_running_projection,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(UsageLedger(tmp_path / "usage.db")),
    )
    bound = threading.Event()
    original_bind = runner._execution_control._bind_driver

    def observe_bind(binding):
        job = runner.get_job(binding.execution_id)
        assert job is not None and job.status is JobStatus.RUNNING
        assert job.cancel_requested_at is not None
        bound.set()
        return original_bind(binding)

    monkeypatch.setattr(runner._execution_control, "_bind_driver", observe_bind)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cancel during activation", agent_id="main",
        )
        assert projection_entered.wait(30)
        assert not bound.is_set()
        assert not fake_worker[3].is_set()

        projected = runner.cancel_execution(job_id)
        assert projected is not None and projected.status is JobStatus.QUEUED
        assert projected.cancel_requested_at is not None

        allow_projection.set()
        assert bound.wait(30)
        assert fake_worker[2].wait(30)
        assert runner.await_job(job_id, timeout=3).status is JobStatus.CANCELLED
    finally:
        allow_projection.set()
        fake_worker[1].set()
        runner.shutdown()


def test_public_cancel_persists_canonical_execution_command(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.runtime.execution import (
        CommandKind,
        CommandStatus,
        default_store as default_execution_store,
    )
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cancel me", agent_id="main",
        )
        assert fake_worker[3].wait(30)
        runner.cancel_execution(job_id)
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
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import CommandStatus, default_store as default_execution_store
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    import openprogram.runtime.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cross-process cancel", agent_id="main",
        )
        assert fake_worker[3].wait(30)

        # The public caller has no process-local JobRunner/DriverRegistry.
        monkeypatch.setattr(runner_module.shared, "_runner", None)
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert fake_worker[2].wait(30)
        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=8,
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
            lambda: ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released",
            timeout=30,
        )
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_public_job_reconciler_recovers_cancel_command_after_apply_failure(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.execution import run_control as execution_run_control
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import CommandStatus, default_store as default_execution_store
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    import openprogram.runtime.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    original_transition = runner._execution_store._transition_command
    fail_apply = True
    job_id: str | None = None

    def fail_first_terminal_cancel_apply(connection, command_id, **kwargs):
        nonlocal fail_apply
        terminal = (
            connection.get("executions", job_id)
            if job_id is not None else None
        )
        if (
            fail_apply
            and command_id == f"execution-cancel:{job_id}"
            and kwargs.get("target") is CommandStatus.APPLIED
            and terminal is not None
            and terminal["status"] == "cancelled"
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
        assert fake_worker[3].wait(30)
        monkeypatch.setattr(runner_module.shared, "_runner", None)
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert fake_worker[2].wait(30)

        assert wait_until(
            lambda: runner.get_job(job_id) is not None
            and runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=8,
        )
        assert wait_until(
            lambda: (
                (command := default_execution_store().get_command(
                    f"execution-cancel:{job_id}",
                )) is not None
                and command.status is CommandStatus.APPLIED
            ),
            timeout=30,
        )
        assert runner._execution_control._delivered_cancel_commands == set()
        assert runner._execution_control._cancel_delivery_by_execution == {}
        def released_admission():
            row = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
            return row is not None and (row["state"], row["reason_code"]) == ("released", "cancel.user")

        assert wait_until(released_admission, timeout=10.0)
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_cross_process_cancel_wins_natural_completion_race(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import CommandStatus, default_store as default_execution_store
    from openprogram.models.usage.ledger import UsageLedger
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult
    from tests.support.waiting import wait_until

    entered = threading.Event()
    release_result = threading.Event()
    finish_started = threading.Event()
    allow_finish = threading.Event()

    def natural_worker(**_kwargs):
        entered.set()
        assert release_result.wait(30)
        return AgentTurnResult(
            final_text="natural result", head_id="natural-head",
        )

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(natural_worker),
    )
    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    import openprogram.runtime.execution.control as control_module
    control_module._default_control_services.clear()
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        budget_poll_seconds=60,
    )
    original_finish = runner._execution_control.finish_attempt

    def delayed_finish(*args, **kwargs):
        finish_started.set()
        assert allow_finish.wait(30)
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(
        runner._execution_control, "finish_attempt", delayed_finish,
    )
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="natural completion race", agent_id="main",
        )
        assert entered.wait(30)
        release_result.set()
        assert finish_started.wait(30)

        # The public caller has no process-local JobRunner/DriverRegistry.
        monkeypatch.setattr(runner_module.shared, "_runner", None)
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        allow_finish.set()

        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=8,
        )
        execution = default_execution_store().get_execution(job_id)
        command = default_execution_store().get_command(
            f"execution-cancel:{job_id}",
        )
        assert execution is not None and execution.status.value == "cancelled"
        assert command is not None and command.status is CommandStatus.APPLIED
        assert execution.reason_code == "cancel.user"
        assert runner.get_job(job_id).reason_code == "cancel.user"
        released = wait_until(
            lambda: _admission_columns(ledger, job_id, "state", "reason_code") == ("released", "cancel.user"),
            timeout=30,
        )
        assert released, _admission_columns(ledger, job_id, "state", "reason_code")
    finally:
        allow_finish.set()
        runner.shutdown()


def test_cross_process_cancel_escalates_to_exact_owner_termination(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    entered = threading.Event()
    release_worker = threading.Event()
    terminated = threading.Event()

    def uncooperative_worker(**_kwargs):
        entered.set()
        assert release_worker.wait(10)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(uncooperative_worker),
    )
    monkeypatch.setattr(runner_module.shared, "_CANCEL_ESCALATION_SECS", 0.01)

    def terminate_process(*_args, **_kwargs):
        terminated.set()
        return True

    monkeypatch.setattr(
        "openprogram.runtime.conversation.process_runner.kill_active_subprocess",
        terminate_process,
    )
    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        budget_poll_seconds=0.01,
    )
    import openprogram.runtime.execution.control as control_module
    control_module._default_control_services.clear()
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="force cross-process cancel", agent_id="main",
        )
        assert entered.wait(30)
        monkeypatch.setattr(runner_module.shared, "_runner", None)
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelling"
        assert terminated.wait(30)
        assert wait_until(
            lambda: runner.get_job(job_id).status is JobStatus.CANCELLED,
            timeout=30,
        )
        execution = runner._execution_store.get_execution(job_id)
        assert execution is not None and execution.status.value == "cancelled"
        assert execution.reason_code == "cancel.user"
        released = wait_until(
            lambda: _admission_columns(ledger, job_id, "state", "reason_code") == ("released", "cancel.user"),
            timeout=30,
        )
        assert released, _admission_columns(ledger, job_id, "state", "reason_code")
    finally:
        release_worker.set()
        runner.shutdown()


def test_transport_neutral_cancel_projects_and_releases_queued_job(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import default_store as default_execution_store
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module.shared, "_runner", runner)
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="queued cancel", agent_id="main",
            defer_dispatch=True,
        )
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelled"
        assert runner.get_job(job_id).status.value == "cancelled"
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released"
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
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import default_store as default_execution_store
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module.shared, "_runner", runner)
    original_update = runner_module.shared._store_update_status
    fail_projection = True

    def fail_cancel_projection(session_id, job_id, status, **fields):
        if fail_projection and status is JobStatus.CANCELLED:
            raise OSError("projection unavailable")
        return original_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(
        runner_module.shared, "_store_update_status", fail_cancel_projection,
    )
    runner2 = None
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="retry projection", agent_id="main",
            defer_dispatch=True,
        )
        result = execution_run_control.cancel_execution(job_id)
        assert result["status"] == "cancelled"
        assert runner.get_job(job_id).status == JobStatus.QUEUED
        assert _admission_columns(ledger, job_id, "state") == ("queued",)
        assert ledger.run_tables().handle.first("job_finalizations", job_id=job_id)["state"] == "pending"
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
        assert _admission_columns(ledger, job_id, "state", "reason_code") == ("released", "cancel.user")
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
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import TerminationReceipt
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    monkeypatch.setattr(runner_module.shared, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(runner_module.shared, "_CANCEL_ESCALATION_SECS", 0.01)
    entered = threading.Event()
    release_worker = threading.Event()

    def hold_worker(**_kwargs):
        entered.set()
        release_worker.wait(10.0)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(hold_worker),
    )
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
        monkeypatch.setattr(
            runner._execution_control, "terminate_attempt", fail_terminate,
        )
        job_id = runner.spawn_job(
            session_id="p1", prompt="terminate failure", agent_id="main",
        )
        assert entered.wait(30)
        runner.cancel_execution(job_id)
        assert wait_until(
            lambda: runner._execution_store.get_execution(job_id).status.value
            == "cancelling",
            timeout=30,
        )
        assert terminate_called.wait(30)
        admission = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
        assert runner._execution_store.get_execution(job_id).status.value == "cancelling"
        assert admission["state"] == "stopping"

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
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released"
    finally:
        release_worker.set()
        runner.shutdown()


def test_cancel_projection_failure_returns_recovery_required_and_blocks_dispatch(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.execution import run_control as execution_run_control
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution import CommandStatus
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    monkeypatch.setattr(runner_module.shared, "_broadcast", lambda *a, **k: None)
    original_update = runner_module.shared._store_update_status
    claim_result: list[object] = []
    claim_done = threading.Event()

    def fail_cancel_projection(session_id, job_id, status, **fields):
        if status is JobStatus.CANCELLED:
            raise OSError("projection unavailable")
        return original_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(
        runner_module.shared, "_store_update_status", fail_cancel_projection,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module.shared, "_runner", runner)

    def fail_enqueue(*_args, **_kwargs):
        def claim_during_recovery():
            claim_result.append(
                runner._governor.claim_next(owner_instance_id=runner._instance_id),
            )
            claim_done.set()

        claim_thread = threading.Thread(target=claim_during_recovery)
        claim_thread.start()
        assert claim_done.wait(30)
        claim_thread.join(timeout=1.0)
        raise OSError("governance unavailable")

    monkeypatch.setattr(
        runner._governor, "enqueue_terminal_projection", fail_enqueue,
    )
    try:
        runner.spawn_job(
            session_id="p1", prompt="occupy worker", agent_id="main",
        )
        assert fake_worker[3].wait(30)
        job_id = runner.spawn_job(
            session_id="p1", prompt="projection failure", agent_id="main",
        )
        result = execution_run_control.cancel_execution(job_id)
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
        admission = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
        assert (admission["state"], admission["dispatch_ready"]) == ("queued", 0)
        assert claim_result == [None]
        assert runner._governor.claim_next(owner_instance_id=runner._instance_id) is None
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_cancel_conflict_records_exact_barrier_and_recovers_deferred_dispatch(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.execution import run_control as execution_run_control
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.execution.store import ExecutionConflict
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module.shared, "_runner", runner)
    original_accept = runner._execution_control.executions.accept_cancel

    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="cancel conflict", agent_id="main",
            defer_dispatch=True,
        )

        def conflict(**_kwargs):
            row = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
            assert _admission_columns(
                ledger, job_id, "state", "dispatch_ready", "terminal_blocked",
                "terminal_block_command_id", "terminal_block_phase",
            ) == (
                "queued", 0, 1, f"execution-cancel:{job_id}", "prepared",
            )
            raise ExecutionConflict("stale_version", "canonical changed")

        monkeypatch.setattr(
            runner._execution_control.executions,
            "accept_cancel", conflict,
        )
        result = execution_run_control.cancel_execution(job_id)
        assert result["recovery_required"] is True
        barrier = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
        assert _admission_columns(
            ledger, job_id, "dispatch_ready", "terminal_blocked", "terminal_block_command_id",
            "terminal_block_phase", "terminal_block_prior_dispatch_ready",
        ) == (
            0, 1, f"execution-cancel:{job_id}", "recovery", 0,
        )

        monkeypatch.setattr(
            runner._execution_control.executions,
            "accept_cancel", original_accept,
        )
        runner._reconcile_terminal_dispatch_barriers()
        assert _admission_columns(ledger, job_id, "dispatch_ready", "terminal_blocked") == (0, 0)
        assert runner._governor.claim_next(
            owner_instance_id=runner._instance_id,
        ) is None
    finally:
        fake_worker[1].set()
        runner.shutdown()


def test_force_termination_releases_exact_owner_after_background_escalation(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    monkeypatch.setattr(runner_module.shared, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(runner_module.shared, "_CANCEL_ESCALATION_SECS", 0.01)
    entered = threading.Event()
    release_worker = threading.Event()
    terminate_called = threading.Event()

    def hold_worker(**_kwargs):
        entered.set()
        release_worker.wait(10.0)
        return AgentTurnResult(final_text="late", head_id="late-head")

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(hold_worker),
    )

    def terminate_process(*_args, **_kwargs):
        terminate_called.set()
        return True

    monkeypatch.setattr(
        "openprogram.runtime.conversation.process_runner.kill_active_subprocess",
        terminate_process,
    )

    ledger = UsageLedger(tmp_path / "termination-success.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="force termination", agent_id="main",
        )
        assert entered.wait(30)
        runner.cancel_execution(job_id)
        assert terminate_called.wait(30)
        assert wait_until(
            lambda: ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released",
            timeout=30,
        )
        assert runner.get_job(job_id).status in {
            JobStatus.CANCELLED, JobStatus.ERRORED,
        }
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["reason_code"] in {"cancel.user", "owner_lost"}
    finally:
        release_worker.set()
        runner.shutdown()


@contextmanager
def _hold_owner_after_canonical_complete(tmp_path, monkeypatch):
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.runtime.conversation.sub_agent_run import AgentTurnResult
    from openprogram.models.usage.ledger import UsageLedger
    from tests.support.waiting import wait_until

    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path",
        lambda: tmp_path / "execution.sqlite3",
    )
    monkeypatch.setattr(runner_module.shared, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(lambda **_k: AgentTurnResult(
            head_id="head_ok", final_text="hello",
        )),
    )
    ledger = None
    runner = None
    allow = threading.Event()
    try:
        ledger = UsageLedger(tmp_path / "usage.db")
        runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
        entered = threading.Event()
        original_wait = runner._wait_for_canonical_driver

        def hold(*args, **kwargs):
            entered.set()
            assert allow.wait(30)
            return original_wait(*args, **kwargs)

        runner._wait_for_canonical_driver = hold
        job_id = runner.spawn_job(
            session_id="p1", prompt="canonical complete", agent_id="main",
        )
        assert entered.wait(30)
        assert wait_until(
            lambda: (
                (execution := runner._execution_store.get_execution(job_id)) is not None
                and execution.status.value == "completed"
            ),
            timeout=30,
        )
        yield runner, ledger, job_id, allow
    finally:
        allow.set()
        try:
            if runner is not None:
                runner.shutdown()
        finally:
            if ledger is not None:
                ledger.close()


def test_reconciler_does_not_absorb_empty_completed_while_local_owner_will_project(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.runtime.jobs.types import JobStatus, is_terminal

    with _hold_owner_after_canonical_complete(tmp_path, monkeypatch) as (
        runner, ledger, job_id, allow,
    ):
        with runner._lock:
            assert job_id in runner._jobs
            assert job_id in runner._done_events
        before = runner.get_job(job_id)
        assert before is not None and not is_terminal(before.status)
        assert before.result_text is None
        admission = ledger.run_tables().handle.first("job_admissions", job_id=job_id)
        assert admission is not None and admission["state"] != "released"

        runner._project_existing_canonical_terminals()

        held = runner.get_job(job_id)
        assert held is not None and not is_terminal(held.status)
        assert held.result_text is None
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] != "released"

        allow.set()
        final = runner.await_job(job_id, timeout=5)
        assert final is not None
        assert final.status is JobStatus.COMPLETED
        assert final.result_text == "hello"
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released"


def test_reconciler_reconstructs_completed_when_local_owner_is_gone(
    tmp_path, store_fixture, monkeypatch,
):
    from openprogram.runtime.jobs.types import JobStatus

    with _hold_owner_after_canonical_complete(tmp_path, monkeypatch) as (
        runner, ledger, job_id, _allow,
    ):
        with runner._lock:
            runner._jobs.pop(job_id, None)
            runner._done_events.pop(job_id, None)

        runner._project_existing_canonical_terminals()

        recovered = runner.get_job(job_id)
        assert recovered is not None
        assert recovered.status is JobStatus.COMPLETED
        assert recovered.result_text is None
        assert ledger.run_tables().handle.first("job_admissions", job_id=job_id)["state"] == "released"


def test_stopped_job_keeps_the_reply_row_it_wrote_as_its_branch_tip(
    tmp_path, store_fixture, fake_worker, monkeypatch,
):
    """A stop projects the Job without a turn result; its branch tip is still
    the reply row admission named for it, when that row was written."""
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.jobs.runner as runner_module
    from openprogram.runtime.jobs.runner import JobRunner
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import UsageLedger

    execution_db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr(
        "openprogram.state.paths.get_run_state_path", lambda: execution_db,
    )
    ledger = UsageLedger(tmp_path / "usage.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr(runner_module.shared, "_runner", runner)
    try:
        wrote = runner.spawn_job(
            session_id="p1", prompt="stopped mid-reply", agent_id="main",
            defer_dispatch=True,
        )
        store_fixture.append_message("p1", {
            "id": f"{wrote}_user_reply", "role": "assistant",
            "content": "partial", "timestamp": 1, "predecessor": "a1",
        })
        store_fixture.commit_turn("p1", "partial reply")
        silent = runner.spawn_job(
            session_id="p1", prompt="stopped before writing", agent_id="main",
            defer_dispatch=True,
        )
        for job_id in (wrote, silent):
            assert execution_run_control.cancel_execution(job_id)["status"] == "cancelled"
        assert runner.get_job(wrote).head_id == f"{wrote}_user_reply"
        assert runner.get_job(silent).head_id is None
    finally:
        fake_worker[1].set()
        runner.shutdown()
