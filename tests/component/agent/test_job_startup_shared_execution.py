"""Job recovery must coexist with foreground Agent executions in one store."""

import pytest

from openprogram.execution import AttemptStore, ExecutionStore
from tests.component.agent.async_job_support import store_fixture  # noqa: F401


@pytest.mark.parametrize("has_foreground_input", [True, False])
def test_job_runner_recovers_foreground_agent_without_job_projection(
    tmp_path, monkeypatch, store_fixture, has_foreground_input,
):
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.usage.ledger import UsageLedger

    db = tmp_path / "execution.sqlite3"
    monkeypatch.setattr("openprogram.paths.get_execution_db_path", lambda: db)
    store = ExecutionStore(db)
    revision = store.create_revision(manifest={"entrypoint": "agent"})
    execution = store.admit_execution(
        session_id="p1", run_id="foreground-run",
        revision_id=revision.revision_id,
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        input_ref="foreground-input", input_hash="foreground-hash",
        config_snapshot_ref="foreground-config", trusted_actor={"subject": "test"},
        agent_turn_payload={
            "version": 1, "kind": "chat",
            "request": {"user_text": "foreground task", "agent_id": "main", "source": "web"},
        } if has_foreground_input else None,
    )

    if not has_foreground_input:
        # Unknown/missing input is not evidence that a Job may skip admission.
        attempts = AttemptStore(store)
        attempt, leased = attempts.lease(
            execution.execution_id, expected_version=execution.status_version,
            owner_id="lost-owner", ttl_seconds=30,
        )
        attempts.activate(
            attempt.attempt_id, generation=attempt.generation,
            expected_execution_version=leased.status_version,
        )
        with pytest.raises(RuntimeError, match="no immutable admission"):
            JobRunner(max_workers=1, governor=ResourceGovernor(
                UsageLedger(tmp_path / "usage.sqlite3"),
            ))
        return

    runner = JobRunner(max_workers=1, governor=ResourceGovernor(
        UsageLedger(tmp_path / "usage.sqlite3"),
    ))
    try:
        recovered = store.get_execution(execution.execution_id)
        assert recovered.status.value == "failed"
        assert recovered.reason_code == "owner_lost_before_activation"
        assert runner.get_job(execution.execution_id) is None
        # Ordinary Job admission remains available after startup recovery.
        job_id = runner.spawn_job(
            session_id="p1", prompt="background task", agent_id="main",
            parent_msg_id="a1", defer_dispatch=True,
        )
        assert runner.get_job(job_id).admission_id
    finally:
        runner.shutdown()
