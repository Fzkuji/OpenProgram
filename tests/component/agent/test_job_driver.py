from __future__ import annotations

import asyncio

import pytest

from openprogram.execution import (
    AttemptStore,
    CapabilitySet,
    DriverRegistry,
    ExecutionStatus,
    RuntimeControlService,
)
from openprogram.execution.driver import DriverRegistryConflict
from openprogram.execution.store import ExecutionStore
from openprogram.agent.job.driver import JobActivationBridge, JobDriver


def _service(tmp_path):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(manifest={"entrypoint": "job"})
    execution = store.create_execution(
        execution_id="job-1",
        run_id="run-1",
        session_id="session-1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(),
    )
    attempts = AttemptStore(store)
    registry = DriverRegistry()
    service = RuntimeControlService(store, attempts, registry)
    return store, attempts, service, execution


def _activate(tmp_path):
    store, attempts, service, execution = _service(tmp_path)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="job-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    driver = JobDriver(execution_id=execution.execution_id)
    bridge = JobActivationBridge(driver)
    delivered, issue = asyncio.run(
        service._activate(
            active,
            None,
            (),
            activator=bridge.activate,
        )
    )
    assert delivered and issue is None
    handle = driver.handle_for(active.attempt_id, active.generation)
    assert handle is not None
    return store, attempts, service, driver, active, running, handle


def test_job_driver_exposes_no_control_capabilities_or_safe_points() -> None:
    driver = JobDriver(execution_id="job-1")

    assert driver.capabilities() == CapabilitySet()


def test_activation_bridge_binds_the_canonical_attempt_identity(tmp_path) -> None:
    store, attempts, service, driver, active, running, handle = _activate(tmp_path)

    binding = service.registry.resolve(
        active.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
    )
    assert binding.driver is driver
    assert binding.handle is handle
    assert handle.execution_id == active.execution_id
    assert handle.attempt_id == active.attempt_id
    assert handle.generation == active.generation


def test_cancel_signals_only_the_exact_active_attempt(tmp_path) -> None:
    store, attempts, service, driver, active, running, handle = _activate(tmp_path)

    dispatch = asyncio.run(
        service.request_cancel(
            command_id="cancel-1",
            execution_id=active.execution_id,
            expected_version=running.status_version,
            actor={"subject": "owner"},
            reason_code="cancel.user",
        )
    )

    assert dispatch.delivered is True
    assert handle.cancel_event.is_set()
    completion = service.finish_attempt(
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_execution_version=dispatch.execution.status_version,
        target=ExecutionStatus.CANCELLED,
        outcome="cancelled",
    )
    assert completion.execution.status is ExecutionStatus.CANCELLED


def test_stale_job_handle_cannot_cancel_a_replaced_attempt(tmp_path) -> None:
    driver = JobDriver(execution_id="job-1")
    first = driver.new_handle("attempt-1", 1)
    second = driver.new_handle("attempt-2", 2)
    assert first is not second

    with pytest.raises(DriverRegistryConflict) as stale:
        asyncio.run(driver.request_cancel(first, "cancel-old"))

    assert stale.value.code == "stale_attempt"
    assert not first.cancel_event.is_set()
    assert not second.cancel_event.is_set()


def test_owner_loss_without_checkpoint_becomes_interrupted(tmp_path) -> None:
    store, attempts, service, execution = _service(tmp_path)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="job-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )

    recovered = service.recover_owner_loss(execution.execution_id)

    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.execution.checkpoint_head_id is None
    assert recovered.execution.current_attempt_id is None
    assert recovered.attempt is not None
    assert recovered.attempt.attempt_id == active.attempt_id
