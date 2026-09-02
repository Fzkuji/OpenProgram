from __future__ import annotations

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
