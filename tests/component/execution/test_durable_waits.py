"""Durable user-input waits are execution-owned control-plane records."""
from __future__ import annotations

import asyncio

import pytest

import openprogram.execution as execution_module
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet, CommandKind
from openprogram.execution.store import ExecutionConflict, ExecutionStore
from openprogram.execution.waits import DurableWaitStore, WaitStatus


def _active_execution(tmp_path):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "chat"})
    execution = executions.create_execution(
        execution_id="exec_wait", run_id="run_wait", session_id="session_wait",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True, state_schema_version=1),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="worker_wait", ttl_seconds=30, attempt_id="attempt_wait",
    )
    attempt, execution = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, attempts, execution, attempt


def test_open_wait_is_execution_owned_and_reconnect_readable(tmp_path) -> None:
    executions, _attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)

    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask",
        request={"prompt": "Continue?", "options": ["yes", "no"]},
        policy_snapshot={"version": 1, "decline": "raise"}, expires_at=9_999_999_999,
    )

    assert wait.status is WaitStatus.OPEN
    assert wait.execution_id == execution.execution_id
    assert wait.attempt_id == attempt.attempt_id
    assert wait.generation == attempt.generation
    restored = DurableWaitStore(ExecutionStore(tmp_path / "executions.db")).get_wait(wait.wait_id)
    assert restored is not None
    assert restored.request["prompt"] == "Continue?"
    assert restored.status is WaitStatus.OPEN


def test_answer_uses_exact_generation_and_is_idempotent(tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="approval",
        request={"prompt": "Allow?"}, policy_snapshot={"version": 1},
        expires_at=9_999_999_999,
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    first = asyncio.run(service.request_wait_answer(
        command_id="answer_1", execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        wait_id=wait.wait_id, generation=wait.claim_generation,
        answer={"answer": "allow", "scope": "once"},
    ))
    retry = asyncio.run(service.request_wait_answer(
        command_id="answer_1", execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        wait_id=wait.wait_id, generation=wait.claim_generation,
        answer={"answer": "allow", "scope": "once"},
    ))

    resolved = waits.get_wait(wait.wait_id)
    assert resolved is not None and resolved.status is WaitStatus.RESOLVED
    assert resolved.answer == {"answer": "allow", "scope": "once"}
    assert first.command.kind is CommandKind.WAIT_ANSWER
    assert retry.command.status.value == "applied"
    with pytest.raises(ExecutionConflict) as raised:
        asyncio.run(service.request_wait_answer(
            command_id="answer_stale", execution_id=execution.execution_id,
            expected_version=execution.status_version, actor={"surface": "test"},
            wait_id=wait.wait_id, generation=wait.claim_generation + 1,
            answer="late",
        ))
    assert raised.value.code == "wait_generation"


def test_reclaim_expired_claim_then_cancel_is_durable(tmp_path) -> None:
    executions, _attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask", request={"prompt": "x"},
        policy_snapshot={"version": 1}, expires_at=9_999_999_999,
    )
    assert waits.claim(wait.wait_id, generation=0, owner_id="dead-owner", lease_ttl_seconds=0.001)
    assert waits.reclaim_expired_claims(now=9_999_999_998) == 1
    reopened = waits.get_wait(wait.wait_id)
    assert reopened is not None and reopened.status is WaitStatus.OPEN
    assert reopened.claim_generation == 1
    assert waits.cancel_execution(execution.execution_id) == 1
    cancelled = waits.get_wait(wait.wait_id)
    assert cancelled is not None and cancelled.status is WaitStatus.CANCELLED


def test_startup_reclaims_claim_after_owner_is_fenced(tmp_path) -> None:
    from openprogram.execution.startup import recover_execution_startup

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask", request={"prompt": "x"},
        policy_snapshot={"version": 1}, expires_at=9_999_999_999,
    )
    assert waits.claim(wait.wait_id, generation=0, owner_id="stopped-worker", lease_ttl_seconds=60)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    result = recover_execution_startup(control_service=service)
    reopened = waits.get_wait(wait.wait_id)
    assert result.waits_reclaimed == 1
    assert reopened is not None and reopened.status is WaitStatus.OPEN
    assert reopened.claim_generation == 1


def test_expired_wait_rejects_command_without_losing_timeout_record(tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask", request={"prompt": "x"},
        policy_snapshot={"version": 1}, expires_at=9_999_999_999,
    )
    with executions._transaction() as connection:
        connection.execute("UPDATE execution_waits SET expires_at = 0 WHERE wait_id = ?", (wait.wait_id,))
    dispatch = asyncio.run(RuntimeControlService(executions, attempts, DriverRegistry()).request_wait_answer(
        command_id="expired_answer", execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        wait_id=wait.wait_id, generation=0, answer="late",
    ))
    assert dispatch.command.status.value == "rejected"
    assert dispatch.command.rejection_code == "wait_expired"
    assert waits.get_wait(wait.wait_id).status is WaitStatus.EXPIRED


def test_ws_public_command_requires_exact_wait_generation(monkeypatch, tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    wait = DurableWaitStore(executions).open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask", request={"prompt": "x"},
        policy_snapshot={"version": 1}, expires_at=9_999_999_999,
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    monkeypatch.setattr(execution_module, "default_store", lambda: executions)
    monkeypatch.setattr(execution_module, "default_control_service", lambda: service)
    from openprogram.webui.ws_actions.runtime import submit_execution_control

    command, updated = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.wait.answer",
            "command_id": "ws_answer", "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
            "payload": {"wait_id": wait.wait_id, "generation": wait.claim_generation, "answer": "yes"},
        },
        "wait_answer", actor={"surface": "test"}, bound_session="session_wait",
    ))
    assert command.kind is CommandKind.WAIT_ANSWER
    assert command.status.value == "applied"
    assert updated.execution_id == execution.execution_id
