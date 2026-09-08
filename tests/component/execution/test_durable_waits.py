"""Durable user-input waits are execution-owned control-plane records."""
from __future__ import annotations

import asyncio

import pytest

import openprogram.execution as execution_module
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.checkpoints import CheckpointFragment
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
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("agent.provider.decision.after",),
            state_schema_version=1,
        ),
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


def test_wait_answers_are_checked_against_request_and_approval_policy(tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    approval = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="approval",
        request={"prompt": "Allow?", "options": ["允许", "拒绝"], "allow_custom": False},
        policy_snapshot={"version": 1, "kind": "approval", "allowed_scopes": ["once"]},
        expires_at=9_999_999_999,
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    for command_id, invalid in (("approval-bool", True), ("approval-scope", {"answer": "approve", "scope": "root"}), ("approval-shape", {"scope": "once"})):
        with pytest.raises(ExecutionConflict) as raised:
            asyncio.run(service.request_wait_answer(
                command_id=command_id, execution_id=execution.execution_id,
                expected_version=execution.status_version, actor={"surface": "test"},
                wait_id=approval.wait_id, generation=approval.claim_generation,
                answer=invalid,
            ))
        assert raised.value.code == "invalid_wait_answer"
    assert waits.get_wait(approval.wait_id).status is WaitStatus.OPEN

    choice = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask",
        request={"prompt": "Pick", "options": ["a"], "multi": False, "allow_custom": False},
        policy_snapshot={"version": 1, "kind": "ask"}, expires_at=9_999_999_999,
    )
    with pytest.raises(ExecutionConflict) as raised:
        waits.resolve_with_command(
            command_id="choice-custom", execution_id=execution.execution_id,
            expected_version=execution.status_version, actor={"surface": "test"},
            kind=CommandKind.WAIT_ANSWER, wait_id=choice.wait_id,
            generation=choice.claim_generation, answer="custom",
        )
    assert raised.value.code == "invalid_wait_answer"


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
        connection.execute("UPDATE execution_waits SET expires_at = 1 WHERE wait_id = ?", (wait.wait_id,))
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
    from openprogram.agent.authority import owner_authority

    command, updated = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.wait.answer",
            "command_id": "ws_answer", "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
            "payload": {"wait_id": wait.wait_id, "generation": wait.claim_generation, "answer": "yes"},
        },
        "wait_answer",
        actor=owner_authority("owner/install/0123456789abcdef"),
        bound_session="session_wait",
    ))
    assert command.kind is CommandKind.WAIT_ANSWER
    assert command.status.value == "applied"
    assert updated.execution_id == execution.execution_id


def test_wait_safe_point_publishes_checkpoint_and_releases_owner_atomically(tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    observed = []
    service.set_wait_suspension_observer(
        lambda suspension: observed.append(
            (suspension.execution.status.value, suspension.execution.current_attempt_id)
        )
    )

    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider:decision", "phase": "after_provider"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="approval",
        request={"prompt": "Allow?"},
        policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        expires_at=9_999_999_999,
        wait_id="wait_safe_point",
    )

    assert suspended.wait.checkpoint_id == suspended.checkpoint.checkpoint_id
    assert suspended.execution.status.value == "paused"
    assert suspended.execution.current_attempt_id is None
    assert attempts.get(attempt.attempt_id).status.value == "ended"
    restored = DurableWaitStore(ExecutionStore(tmp_path / "executions.db")).get_wait("wait_safe_point")
    assert restored is not None
    assert restored.checkpoint_id == suspended.checkpoint.checkpoint_id
    assert restored.status is WaitStatus.OPEN
    assert observed == [("paused", None)]


def test_answer_reactivates_exact_wait_checkpoint_without_replaying_wait_attempt(tmp_path) -> None:
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activated = []

    async def activate(next_attempt, activation):
        activated.append((next_attempt, activation.checkpoint))
        return None

    service = RuntimeControlService(
        executions, attempts, DriverRegistry(), activator=activate,
    )
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider:decision", "phase": "after_provider"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="ask",
        request={"prompt": "Continue?"},
        policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        expires_at=9_999_999_999,
        wait_id="wait_resume",
    )

    dispatch = asyncio.run(service.request_wait_answer(
        command_id="answer_resume", execution_id=execution.execution_id,
        expected_version=suspended.execution.status_version, actor={"surface": "test"},
        wait_id=suspended.wait.wait_id, generation=suspended.wait.claim_generation,
        answer="yes",
    ))

    assert dispatch.command.status.value == "applied"
    assert dispatch.execution.status.value == "running"
    assert len(activated) == 1
    next_attempt, checkpoint = activated[0]
    assert next_attempt.attempt_id != attempt.attempt_id
    assert checkpoint.checkpoint_id == suspended.checkpoint.checkpoint_id
    assert DurableWaitStore(executions).get_wait("wait_resume").outcome == "answered"


@pytest.mark.parametrize("resume_blocked", [False, True])
def test_startup_recovers_committed_wait_outcome_once(tmp_path, resume_blocked) -> None:
    from openprogram.execution.startup import recover_execution_startup

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activations = []

    async def activate(next_attempt, activation):
        activations.append((next_attempt.attempt_id, activation.checkpoint.checkpoint_id))
        return None

    service = RuntimeControlService(executions, attempts, DriverRegistry(), activator=activate)
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider:decision", "phase": "after_provider"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="ask",
        request={"prompt": "Continue?"},
        policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        expires_at=9_999_999_999,
        wait_id="wait_restart",
    )
    DurableWaitStore(executions).resolve_with_command(
        command_id="answer_before_restart", execution_id=execution.execution_id,
        expected_version=suspended.execution.status_version, actor={"surface": "test"},
        kind=CommandKind.WAIT_ANSWER, wait_id="wait_restart", generation=0,
        answer="yes",
    )

    if resume_blocked:
        # A prior resume already checked and rejected an incompatible runtime.
        # The old answer must not repeatedly trigger that operation on restart.
        with executions._transaction() as connection:
            connection.execute(
                "UPDATE executions SET reason_code = 'continuation_contract_mismatch' WHERE execution_id = ?",
                (execution.execution_id,),
            )
    recover_execution_startup(control_service=service)
    # The saga scan is safe to repeat while the resumed owner is active.
    asyncio.run(service.recover_wait_outcomes())

    if resume_blocked:
        assert activations == []
        assert executions.get_execution(execution.execution_id).reason_code == "continuation_contract_mismatch"
        return
    assert len(activations) == 1
    assert activations[0][1] == suspended.checkpoint.checkpoint_id
    assert executions.get_execution(execution.execution_id).status.value == "running"


def test_timeout_policy_is_persisted_and_settled_on_startup(tmp_path) -> None:
    from openprogram.execution.startup import recover_execution_startup

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider:decision", "phase": "after_provider"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind="approval",
        request={"prompt": "Allow?"},
        policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        expires_at=9_999_999_999,
        wait_id="wait_timeout",
    )
    with executions._transaction() as connection:
        connection.execute("UPDATE execution_waits SET expires_at = 1 WHERE wait_id = ?", (suspended.wait.wait_id,))

    recover_execution_startup(control_service=service)

    timed_out = DurableWaitStore(executions).get_wait("wait_timeout")
    assert timed_out is not None and timed_out.status is WaitStatus.EXPIRED
    assert executions.get_execution(execution.execution_id).status.value == "failed"


def test_no_deadline_wait_survives_long_delay_and_store_reopen(tmp_path):
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    waits = DurableWaitStore(executions)
    wait = waits.open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind='approval',
        request={'prompt': 'Allow?'}, policy_snapshot={'version': 1}, expires_at=0,
    )
    restored = DurableWaitStore(ExecutionStore(tmp_path / 'executions.db'))
    assert restored.expire_due(now=wait.created_at + 86400 * 365) == 0
    assert restored.get_wait(wait.wait_id).status is WaitStatus.OPEN
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    result = asyncio.run(service.request_wait_answer(
        command_id='long_wait_answer', execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={'surface': 'test'},
        wait_id=wait.wait_id, generation=0, answer={'answer': 'allow', 'scope': 'once'},
    ))
    assert result.command.status.value == 'applied'


def test_system_access_wait_is_worker_resolved_and_cannot_accept_question_answer(tmp_path):
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, expected_version=execution.status_version,
        fragment=_decision_fragment(), kind="system_access",
        request={"tool": "gui_agent", "required_capabilities": ["screen_recording"]},
        policy_snapshot={"version": 1, "kind": "system_access", "on_grant": "continue"},
        expires_at=0, wait_id="wait_system_access",
    )
    waits = DurableWaitStore(executions)

    with pytest.raises(ExecutionConflict) as raised:
        waits.resolve_with_command(
            command_id="system-answer", execution_id=execution.execution_id,
            expected_version=suspended.execution.status_version, actor={"surface": "test"},
            kind=CommandKind.WAIT_ANSWER, wait_id=suspended.wait.wait_id,
            generation=suspended.wait.claim_generation, answer={"answer": "allow", "scope": "once"},
        )
    assert raised.value.code == "invalid_wait_command"
    assert waits.get_wait(suspended.wait.wait_id).status is WaitStatus.OPEN
    with pytest.raises(ExecutionConflict) as declined:
        waits.resolve_with_command(
            command_id="system-decline", execution_id=execution.execution_id,
            expected_version=suspended.execution.status_version, actor={"surface": "test"},
            kind=CommandKind.WAIT_DECLINE, wait_id=suspended.wait.wait_id,
            generation=suspended.wait.claim_generation, answer="no",
        )
    assert declined.value.code == "invalid_wait_command"

    resolved = waits.resolve_system_access(
        suspended.wait.wait_id,
        report={"capabilities": [{"id": "screen_recording", "status": "granted"}]},
        owner_id="worker-test",
    )
    assert resolved is not None
    assert resolved.status is WaitStatus.RESOLVED
    assert resolved.outcome == "granted"
    restored = DurableWaitStore(ExecutionStore(tmp_path / "executions.db")).get_wait(
        suspended.wait.wait_id
    )
    assert restored is not None and restored.outcome == "granted"


def test_system_access_wait_unknown_or_cancelled_never_resumes(tmp_path):
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, expected_version=execution.status_version,
        fragment=_decision_fragment(), kind="system_access",
        request={"tool": "gui_agent", "required_capabilities": ["accessibility"]},
        policy_snapshot={"version": 1, "kind": "system_access", "on_grant": "continue"},
        expires_at=0, wait_id="wait_system_cancelled",
    )
    waits = DurableWaitStore(executions)
    assert waits.resolve_system_access(
        suspended.wait.wait_id,
        report={"capabilities": [{"id": "accessibility", "status": "unknown"}]},
        owner_id="worker-test",
    ) is None
    assert waits.get_wait(suspended.wait.wait_id).status is WaitStatus.OPEN
    assert waits.cancel_execution(execution.execution_id) == 1
    assert waits.resolve_system_access(
        suspended.wait.wait_id,
        report={"capabilities": [{"id": "accessibility", "status": "granted"}]},
        owner_id="worker-test",
    ) is None
    assert waits.get_wait(suspended.wait.wait_id).status is WaitStatus.CANCELLED


def test_system_access_grant_recovery_resumes_same_checkpoint_once(tmp_path):
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activations = []

    async def activate(next_attempt, activation):
        activations.append((next_attempt.attempt_id, activation.checkpoint.checkpoint_id))

    service = RuntimeControlService(
        executions, attempts, DriverRegistry(), activator=activate,
    )
    suspended = service.open_wait_at_safe_point(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, expected_version=execution.status_version,
        fragment=_decision_fragment(), kind="system_access",
        request={"tool": "gui_agent", "required_capabilities": ["screen_recording"]},
        policy_snapshot={"version": 1, "kind": "system_access", "on_grant": "continue"},
        expires_at=0, wait_id="wait_system_resume",
    )
    waits = DurableWaitStore(executions)
    waits.resolve_system_access(
        suspended.wait.wait_id,
        report={"capabilities": [{"id": "screen_recording", "status": "granted"}]},
        owner_id="worker-test",
    )

    asyncio.run(service.recover_wait_outcomes())
    asyncio.run(service.recover_wait_outcomes())
    assert len(activations) == 1
    assert activations[0][1] == suspended.checkpoint.checkpoint_id


@pytest.mark.parametrize('change', ['unchanged', 'modified', 'deleted'])
def test_file_approval_rechecks_durable_state_on_resume(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from openprogram.agent.permissions.approval import await_user_approval
    from openprogram.agent.permissions.file_state import capture
    from openprogram.agent.run_control import set_preapproved_wait_id, reset_preapproved_wait_id
    path = tmp_path / 'approved.txt'
    path.write_text('original')
    args = {'file_path': str(path), 'content': 'replacement'}
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    wait = DurableWaitStore(executions).open_wait(
        execution_id=execution.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind='approval', expires_at=0,
        request={'prompt': 'Allow?', 'tool': 'write', 'args': args,
                 'file_preconditions': capture('write', args)},
        policy_snapshot={'version': 1},
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    asyncio.run(service.request_wait_answer(
        command_id='file_answer', execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={'surface': 'test'},
        wait_id=wait.wait_id, generation=0, answer={'answer': 'approve', 'scope': 'once'},
    ))
    if change == 'modified': path.write_text('user contents')
    if change == 'deleted': path.unlink()
    monkeypatch.setattr(execution_module, 'default_store', lambda: ExecutionStore(tmp_path / 'executions.db'))
    token = set_preapproved_wait_id(wait.wait_id)
    try:
        approved, reason, _ = asyncio.run(await_user_approval(
            req=SimpleNamespace(session_id=execution.session_id), tool_name='write', args=args, on_event=lambda _: None,
        ))
    finally:
        reset_preapproved_wait_id(token)
    assert approved is (change == 'unchanged')
    if change == 'deleted':
        assert 'no longer exists' in reason
        assert not path.exists()
    if change == 'modified': assert path.read_text() == 'user contents'


def _decision_fragment(step_id: str = "provider:decision") -> CheckpointFragment:
    return CheckpointFragment(
        safe_point_kind="agent.provider.decision.after",
        frontier=({"step_id": step_id, "phase": "after_provider"},),
        state_refs={"continuation": {"version": 1}},
    )


def _suspend_ask(service, execution, attempt, wait_id, *, step_id="provider:decision", policy=None):
    return service.open_wait_at_safe_point(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_version=execution.status_version,
        fragment=_decision_fragment(step_id),
        kind="ask",
        request={"prompt": "Continue?"},
        policy_snapshot=policy or {
            "version": 1,
            "on_answer": "continue",
            "on_decline": "fail",
            "on_timeout": "fail",
        },
        expires_at=9_999_999_999,
        wait_id=wait_id,
    )


def _running_attempt(attempts, executions, execution_id):
    current = executions.get_execution(execution_id)
    assert current is not None and current.current_attempt_id is not None
    attempt = attempts.get(current.current_attempt_id)
    assert attempt is not None
    return current, attempt


def test_startup_ignores_historical_wait_resume_and_recovers_current_wait(
    tmp_path,
) -> None:
    from openprogram.execution.startup import recover_execution_startup

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activations = []

    async def activate(next_attempt, activation):
        activations.append((next_attempt.attempt_id, activation.checkpoint.checkpoint_id))
        return None

    service = RuntimeControlService(
        executions, attempts, DriverRegistry(), activator=activate,
    )
    first = _suspend_ask(service, execution, attempt, "wait_a")
    asyncio.run(service.request_wait_answer(
        command_id="answer_a", execution_id=execution.execution_id,
        expected_version=first.execution.status_version, actor={"surface": "test"},
        wait_id="wait_a", generation=first.wait.claim_generation, answer="yes",
    ))
    running, next_attempt = _running_attempt(attempts, executions, execution.execution_id)
    second = _suspend_ask(
        service, running, next_attempt, "wait_b", step_id="provider:decision:b",
    )
    first_resume = executions.get_command("wait-resume:wait_a:answered")
    assert first_resume is not None and first_resume.status.value == "applied"
    assert second.checkpoint.checkpoint_id != first.checkpoint.checkpoint_id
    assert len(activations) == 1

    recover_execution_startup(control_service=service)
    asyncio.run(service.recover_wait_outcomes())

    latest = executions.get_execution(execution.execution_id)
    assert latest is not None
    assert latest.status.value == "paused"
    assert latest.reason_code == "wait_open"
    assert latest.checkpoint_head_id == second.checkpoint.checkpoint_id
    assert latest.current_attempt_id is None
    assert DurableWaitStore(executions).get_wait("wait_b").status is WaitStatus.OPEN
    assert len(activations) == 1
    replayed = executions.get_command("wait-resume:wait_a:answered")
    assert replayed is not None
    assert replayed.expected_version == first_resume.expected_version
    assert replayed.status.value == "applied"

    DurableWaitStore(executions).resolve_with_command(
        command_id="answer_b_before_restart", execution_id=execution.execution_id,
        expected_version=latest.status_version, actor={"surface": "test"},
        kind=CommandKind.WAIT_ANSWER, wait_id="wait_b", generation=0,
        answer="yes",
    )
    recover_execution_startup(control_service=service)
    asyncio.run(service.recover_wait_outcomes())

    resumed = executions.get_execution(execution.execution_id)
    assert resumed is not None and resumed.status.value == "running"
    assert len(activations) == 2
    assert activations[1][1] == second.checkpoint.checkpoint_id


@pytest.mark.parametrize("stale_kind", ["fail", "cancel", "scheduler", "null_checkpoint"])
def test_stale_wait_outcome_does_not_own_later_wait_frontier(tmp_path, stale_kind) -> None:
    from openprogram.execution.startup import recover_execution_startup

    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activations = []
    scheduled = []

    async def activate(next_attempt, activation):
        activations.append(next_attempt.attempt_id)
        return None

    service = RuntimeControlService(
        executions, attempts, DriverRegistry(), activator=activate,
    )
    if stale_kind in {"fail", "cancel"}:
        first = _suspend_ask(
            service, execution, attempt, "wait_a",
            policy={
                "version": 1,
                "on_answer": "continue",
                "on_decline": stale_kind,
                "on_timeout": "fail",
            },
        )
        DurableWaitStore(executions).resolve_with_command(
            command_id="decline_a", execution_id=execution.execution_id,
            expected_version=first.execution.status_version, actor={"surface": "test"},
            kind=CommandKind.WAIT_DECLINE, wait_id="wait_a", generation=0,
        )
        asyncio.run(service.request_continue(
            command_id="continue_after_stale_a",
            execution_id=execution.execution_id,
            expected_version=first.execution.status_version,
            actor={"surface": "test"},
        ))
    else:
        first = _suspend_ask(service, execution, attempt, "wait_a")
        asyncio.run(service.request_wait_answer(
            command_id="answer_a", execution_id=execution.execution_id,
            expected_version=first.execution.status_version, actor={"surface": "test"},
            wait_id="wait_a", generation=first.wait.claim_generation, answer="yes",
        ))
    running, next_attempt = _running_attempt(attempts, executions, execution.execution_id)
    second = _suspend_ask(
        service, running, next_attempt, "wait_b", step_id="provider:decision:b",
    )
    if stale_kind == "null_checkpoint":
        with executions._transaction() as connection:
            connection.execute(
                "UPDATE execution_waits SET checkpoint_id = NULL WHERE wait_id = ?",
                ("wait_a",),
            )
    if stale_kind == "scheduler":
        service.set_wait_resume_scheduler(lambda wait, _execution: scheduled.append(wait.wait_id))

    recover_execution_startup(control_service=service)
    asyncio.run(service.recover_wait_outcomes())

    latest = executions.get_execution(execution.execution_id)
    assert latest is not None
    assert latest.status.value == "paused"
    assert latest.reason_code == "wait_open"
    assert latest.checkpoint_head_id == second.checkpoint.checkpoint_id
    assert DurableWaitStore(executions).get_wait("wait_b").status is WaitStatus.OPEN
    assert scheduled == []
    assert executions.get_command("wait-cancel:wait_a:declined") is None


@pytest.mark.parametrize("answer_latest", [False, True])
def test_recovery_only_applies_outcome_for_current_wait_checkpoint(tmp_path, answer_latest):
    executions, attempts, execution, attempt = _active_execution(tmp_path)
    activated = []

    async def activate(next_attempt, activation):
        activated.append(next_attempt)

    service = RuntimeControlService(executions, attempts, DriverRegistry(), activator=activate)

    def suspend(current, owner, wait_id):
        return service.open_wait_at_safe_point(
            execution_id=current.execution_id, attempt_id=owner.attempt_id,
            generation=owner.generation, expected_version=current.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="agent.provider.decision.after",
                frontier=({"step_id": wait_id, "phase": "after_provider"},),
                state_refs={"continuation": {"version": 1}},
            ),
            kind="ask", request={"prompt": wait_id},
            policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail"},
            expires_at=0, wait_id=wait_id,
        )

    first = suspend(execution, attempt, "first")
    answered = asyncio.run(service.request_wait_answer(
        command_id="answer-first", execution_id=execution.execution_id,
        expected_version=first.execution.status_version, actor={"surface": "test"},
        wait_id="first", generation=0, answer="yes",
    ))
    second = suspend(answered.execution, activated[-1], "second")
    if answer_latest:
        DurableWaitStore(executions).resolve_with_command(
            command_id="answer-second", execution_id=execution.execution_id,
            expected_version=second.execution.status_version, actor={"surface": "test"},
            kind=CommandKind.WAIT_ANSWER, wait_id="second", generation=0, answer="yes",
        )
    asyncio.run(service.recover_wait_outcomes())
    asyncio.run(service.recover_wait_outcomes())
    current = executions.get_execution(execution.execution_id)
    assert len(activated) == (2 if answer_latest else 1)
    assert current.checkpoint_head_id == second.checkpoint.checkpoint_id
    assert current.status.value == ("running" if answer_latest else "paused")
    assert DurableWaitStore(executions).get_wait("second").status.value == (
        "resolved" if answer_latest else "open"
    )
