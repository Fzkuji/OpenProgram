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
