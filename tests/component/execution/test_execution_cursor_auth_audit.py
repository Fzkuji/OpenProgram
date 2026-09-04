from __future__ import annotations

import pytest

from openprogram.agent.authority import owner_authority
from openprogram.execution import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ExecutionStatus,
    ExecutionStore,
)


def _store(tmp_path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")


def _admit(store: ExecutionStore, execution_id: str, session_id: str):
    revision = store.create_revision(
        revision_id=f"revision-{execution_id}",
        manifest={"entrypoint": "workflow.run", "execution_id": execution_id},
    )
    return store.admit_execution(
        execution_id=execution_id,
        run_id=f"run-{execution_id}",
        session_id=session_id,
        revision_id=revision.revision_id,
        input_ref=f"blob:{execution_id}",
        input_hash=f"hash:{execution_id}",
        entrypoint="workflow.run",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config",
        capabilities=CapabilitySet(pause=True),
    )


def test_event_replay_uses_a_contiguous_execution_cursor_not_global_sqlite_ids(tmp_path):
    store = _store(tmp_path)
    first = _admit(store, "exec-first", "session-first")
    second = _admit(store, "exec-second", "session-second")
    store.transition_execution(
        first.execution_id,
        expected_version=first.status_version,
        target=ExecutionStatus.RUNNING,
    )
    store.transition_execution(
        second.execution_id,
        expected_version=second.status_version,
        target=ExecutionStatus.RUNNING,
    )

    replay = store.read_event_replay(first.execution_id, after_sequence=0)

    assert [event.execution_sequence for event in replay.events] == [1, 2]
    assert replay.cursor.next_sequence == 3
    assert replay.recovery is None


def test_event_replay_requires_snapshot_for_an_invalid_or_expired_cursor(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store, "exec-1", "session-1")

    replay = store.read_event_replay(execution.execution_id, after_sequence=99)

    assert replay.events == ()
    assert replay.recovery == "cursor_ahead"
    assert replay.cursor.next_sequence == 2


def test_execution_authorization_hides_cross_scope_and_allows_exact_owner_scope(tmp_path):
    from openprogram.execution.authorization import (
        ExecutionAuthorizationError,
        authorize_execution_action,
    )

    store = _store(tmp_path)
    execution = _admit(store, "exec-1", "session-1")
    owner = owner_authority("owner/install/0123456789abcdef")

    decision = authorize_execution_action(
        {**owner, "project_ids": ["project-a"], "session_ids": ["session-1"]},
        "execution.snapshot",
        execution,
        {"project_id": "project-a", "session_id": "session-1"},
    )
    assert decision.allowed is True
    assert decision.policy_version == "execution-policy-v1"

    with pytest.raises(ExecutionAuthorizationError) as denied:
        authorize_execution_action(
            {**owner, "project_ids": ["project-b"]},
            "execution.snapshot",
            execution,
            {"project_id": "project-a", "session_id": "session-1"},
        )
    assert denied.value.code == "not_found"


def test_command_audit_redacts_secrets_and_query_requires_execution_read_scope(tmp_path):
    from openprogram.execution.authorization import ExecutionAuthorizationError

    store = _store(tmp_path)
    execution = _admit(store, "exec-1", "session-1")
    owner = owner_authority("owner/install/0123456789abcdef")

    event = store.append_audit_event(
        execution_id=execution.execution_id,
        actor=owner,
        action="execution.steer",
        result="accepted",
        surface="ws",
        payload={"prompt": "private", "token": "secret", "safe": "visible"},
    )

    assert event.redacted_payload["prompt"]["redacted"] is True
    assert event.redacted_payload["token"]["redacted"] is True
    assert event.redacted_payload["safe"] == "visible"
    assert store.list_audit_events(execution.execution_id, actor=owner)
    with pytest.raises(ExecutionAuthorizationError) as denied:
        store.list_audit_events(
            execution.execution_id,
            actor={**owner, "session_ids": ["session-elsewhere"]},
        )
    assert denied.value.code == "not_found"


def test_control_command_appends_accepted_and_terminal_audit_records(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store, "exec-1", "session-1")
    owner = owner_authority("owner/install/0123456789abcdef")

    command = store.accept_command(
        command_id="cmd-1",
        execution_id=execution.execution_id,
        expected_version=execution.status_version,
        kind=CommandKind.PAUSE,
        payload={"reason_code": "pause.user", "prompt": "must not persist"},
        actor=owner,
    )
    store.transition_command(
        command.command_id,
        expected_status=command.status,
        target=CommandStatus.REJECTED,
        rejection_code="superseded",
    )

    events = store.list_audit_events(execution.execution_id, actor=owner)
    assert [event.result for event in events] == ["accepted", "rejected"]
    assert events[0].command_id == command.command_id
    assert events[0].redacted_payload["prompt"]["redacted"] is True
