"""An unattended stage whose model request never settled ends instead of waiting."""
from types import SimpleNamespace

import pytest

from openprogram.runtime.jobs.types import JobStatus

JOB_ID = "self-update:su_test:verify:1"


def _verifying(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.runtime.execution import AttemptStore, ExecutionStore
    from openprogram.runtime.execution.model import CapabilitySet
    from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase
    from tests.unit.self_update.test_store import _request

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    updates = SelfUpdateStore()
    updates.create(_request())
    for phase in (UpdatePhase.BUILDING, UpdatePhase.READY, UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
        updates.transition("su_test", phase)
    executions = ExecutionStore(tmp_path / "run-state")
    revision = executions.create_revision(manifest={"entrypoint": "test"})
    execution = executions.create_execution(
        execution_id=JOB_ID, run_id="run", session_id="session", revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True, safe_point_kinds=("action.after",), state_schema_version=1),
    )
    attempts = AttemptStore(executions)
    leased, execution = attempts.lease(JOB_ID, expected_version=execution.status_version,
                                       owner_id="worker")
    attempt, _ = attempts.activate(leased.attempt_id, generation=leased.generation,
                                   expected_execution_version=execution.status_version)
    cancelled = []
    runner = SimpleNamespace(_execution_store=executions,
                             cancel_execution=lambda job_id, reason: cancelled.append(job_id))
    monkeypatch.setattr("openprogram.runtime.jobs.store.load_job",
                        lambda session_id, job_id: SimpleNamespace(status=JobStatus.RUNNING))
    return runner, attempts, attempt, cancelled


@pytest.mark.parametrize("parked", [True, False])
def test_a_verifier_whose_request_never_settled_is_cancelled(tmp_path, monkeypatch, parked):
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.operations.self_update.control.recovery import stop_parked_stages

    runner, attempts, attempt, cancelled = _verifying(tmp_path, monkeypatch)
    if parked:
        current = runner._execution_store.get_execution(JOB_ID)
        attempts.finish(attempt.attempt_id, generation=attempt.generation,
                        expected_execution_version=current.status_version,
                        target=ExecutionStatus.RECONCILIATION_REQUIRED,
                        outcome="reconciliation_required", reason_code="effect_reconciliation")

    assert stop_parked_stages(runner) == ([JOB_ID] if parked else [])
    assert cancelled == ([JOB_ID] if parked else []), "A running verifier is left alone"
