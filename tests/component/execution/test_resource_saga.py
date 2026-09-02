"""Crash-replay contracts for the execution/resource SQLite hand-off."""

from __future__ import annotations

import pytest

from openprogram.agent.job.types import Job
from openprogram.agent.resource_governance import ResourceGovernor, ResourceLimits, resolve_resource_limits
from openprogram.execution import CapabilitySet, ExecutionStore, ResourceSaga
from openprogram.usage.ledger import UsageLedger


@pytest.fixture(autouse=True)
def worker_lock(monkeypatch):
    monkeypatch.setattr("openprogram.worker.lock.is_held_by", lambda _pid: True)


@pytest.fixture
def saga_parts(tmp_path):
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    revision = store.create_revision(revision_id="revision", manifest={"kind": "job"})
    store.admit_execution(
        execution_id="job-1", session_id="session-1", revision_id=revision.revision_id,
        input_ref="input", input_hash="hash", entrypoint="test", trusted_actor={},
        config_snapshot_ref="config", capabilities=CapabilitySet(),
    )
    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    governor = ResourceGovernor(
        UsageLedger(tmp_path / "usage.sqlite3"),
        limit_resolver=lambda _session, _job: resolved,
    )
    return store, governor


def job() -> Job:
    return Job(id="job-1", parent_session_id="session-1", prompt="p", agent_id="a")


def admit_and_replay(store, governor) -> str:
    saga = ResourceSaga(store, governor, owner_id="saga-owner")
    admission_id = saga.admit("job-1", job())
    assert saga.reconcile() == 2
    return admission_id


def test_execution_written_crash_replays_one_admission_without_duplicate_capacity(saga_parts):
    store, governor = saga_parts

    def crash(point: str) -> None:
        if point == "execution_written":
            raise RuntimeError("crash after execution commit")

    with pytest.raises(RuntimeError, match="execution commit"):
        ResourceSaga(store, governor, owner_id="saga-owner", fault_hook=crash).admit("job-1", job())

    assert len(store.list_resource_intents(execution_id="job-1")) == 2
    assert ResourceSaga(store, governor, owner_id="saga-owner").reconcile() == 2
    assert governor.ledger.connection().execute(
        "SELECT COUNT(*) FROM job_admissions WHERE job_id = 'job-1'"
    ).fetchone()[0] == 1


def test_governor_preparing_crash_leaves_pending_intent_for_startup_replay(saga_parts):
    store, governor = saga_parts
    saga = ResourceSaga(store, governor, owner_id="saga-owner")
    saga.admit("job-1", job())

    def crash(point: str) -> None:
        if point == "governor_preparing":
            raise RuntimeError("crash before governor commit")

    with pytest.raises(RuntimeError, match="governor commit"):
        ResourceSaga(store, governor, owner_id="saga-owner", fault_hook=crash).reconcile()

    ResourceSaga(store, governor, owner_id="saga-owner").reconcile()
    row = governor.ledger.connection().execute(
        "SELECT state FROM job_admissions WHERE job_id = 'job-1'"
    ).fetchone()
    assert row["state"] == "queued"


def test_claim_and_release_replay_are_fenced_and_idempotent(saga_parts):
    store, governor = saga_parts
    admission_id = admit_and_replay(store, governor)
    saga = ResourceSaga(store, governor, owner_id="saga-owner")
    saga.request_claim("job-1", admission_id=admission_id, command_id="continue-1")
    saga.reconcile()
    claim = next(
        item for item in store.list_resource_intents(execution_id="job-1")
        if item["kind"] == "resource.claim.intent"
    )
    generation = claim["result"]["resource_lease_generation"]
    assert generation == 1

    saga.request_release(
        "job-1", admission_id=admission_id, reason_code="pause.user",
        attempt_id="attempt-1", generation=1, resource_lease_generation=generation,
    )
    saga.reconcile()
    saga.reconcile()
    row = governor.ledger.connection().execute(
        "SELECT state, lease_generation FROM job_admissions WHERE job_id = 'job-1'"
    ).fetchone()
    assert row["state"] == "released"
    assert row["lease_generation"] == generation


def test_resume_wait_and_terminal_release_are_durable(saga_parts):
    store, governor = saga_parts
    admission_id = admit_and_replay(store, governor)
    saga = ResourceSaga(store, governor, owner_id="saga-owner")
    saga.request_release("job-1", admission_id=admission_id, reason_code="pause.user")
    saga.reconcile()
    saga.request_claim("job-1", admission_id=admission_id, command_id="continue-2")
    claim_intent = next(
        item for item in store.list_resource_intents(execution_id="job-1")
        if item["idempotency_key"].endswith("continue-2") and item["kind"] == "resource.claim.intent"
    )
    assert claim_intent["state"] == "pending"
    saga.reconcile()
    lease_generation = next(
        item["result"]["resource_lease_generation"]
        for item in store.list_resource_intents(execution_id="job-1")
        if item["kind"] == "resource.claim.intent" and item["idempotency_key"].endswith("continue-2")
    )
    saga.request_release(
        "job-1", admission_id=admission_id, reason_code="completed",
        resource_lease_generation=lease_generation, terminal_version=3,
    )
    saga.reconcile()
    assert governor.ledger.resource_counts("session-1", "job-1")["resource_state"] == "released"
