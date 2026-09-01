from __future__ import annotations

import pytest

from openprogram.execution.model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ExecutionStatus,
)
from openprogram.execution.store import (
    CommandConflict,
    ExecutionConflict,
    ExecutionStore,
    _store_for_path,
    default_store,
)


def _store(tmp_path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")


def _execution(store: ExecutionStore, **overrides):
    values = {
        "execution_id": "exec_1",
        "run_id": "run_1",
        "session_id": "session_1",
        "revision_id": "revision_1",
        "capabilities": CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=("action.before", "action.after"),
            state_schema_version=1,
        ),
    }
    values.update(overrides)
    revision_id = values["revision_id"]
    if store.get_revision(revision_id) is None:
        store.create_revision(
            revision_id=revision_id,
            manifest={"entrypoint": revision_id, "schema": 1},
        )
    return store.create_execution(**values)


def test_revision_is_content_addressed_immutable_and_durable(tmp_path) -> None:
    path = tmp_path / "runtime" / "executions.sqlite3"
    store = ExecutionStore(path)
    created = store.create_revision(
        manifest={"entrypoint": "research.workflow", "tools": ["search"]}
    )

    assert created.revision_id.startswith("rev_")
    assert ExecutionStore(path).get_revision(created.revision_id) == created
    assert (
        store.create_revision(
            manifest={"entrypoint": "research.workflow", "tools": ["search"]}
        )
        == created
    )

    with pytest.raises(ExecutionConflict) as collision:
        store.create_revision(
            revision_id=created.revision_id,
            manifest={"entrypoint": "different.workflow"},
        )
    assert collision.value.code == "revision_id_collision"


def test_execution_requires_a_registered_revision(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ExecutionConflict) as missing:
        store.create_execution(
            execution_id="exec_missing_revision",
            run_id="run_1",
            session_id="session_1",
            revision_id="rev_missing",
        )
    assert missing.value.code == "revision_not_found"


def test_run_identity_is_bound_to_one_session(tmp_path) -> None:
    store = _store(tmp_path)
    _execution(store)
    with pytest.raises(ExecutionConflict) as mismatch:
        _execution(
            store,
            execution_id="exec_other_session",
            session_id="session_2",
        )
    assert mismatch.value.code == "run_identity_mismatch"


def test_create_is_durable_and_rebuildable_from_append_only_events(tmp_path) -> None:
    path = tmp_path / "runtime" / "executions.sqlite3"
    created = _execution(ExecutionStore(path))

    reopened = ExecutionStore(path)
    assert reopened.get_execution(created.execution_id) == created
    assert reopened.rebuild_execution(created.execution_id) == created
    events = reopened.list_events(created.execution_id)
    assert [event.kind for event in events] == ["execution.created"]
    assert events[0].execution_version == 1
    assert events[0].schema_version == 1


def test_transition_uses_version_cas_and_terminal_state_is_immutable(tmp_path) -> None:
    store = _store(tmp_path)
    created = _execution(store)
    running = store.transition_execution(
        created.execution_id,
        expected_version=created.status_version,
        target=ExecutionStatus.RUNNING,
    )
    completed = store.transition_execution(
        running.execution_id,
        expected_version=running.status_version,
        target=ExecutionStatus.COMPLETED,
        reason_code="result_committed",
    )

    with pytest.raises(ExecutionConflict):
        store.transition_execution(
            completed.execution_id,
            expected_version=running.status_version,
            target=ExecutionStatus.CANCELLING,
        )
    with pytest.raises(ExecutionConflict) as terminal:
        store.transition_execution(
            completed.execution_id,
            expected_version=completed.status_version,
            target=ExecutionStatus.CANCELLING,
        )
    assert terminal.value.code == "terminal"
    assert store.get_execution(completed.execution_id) == completed


def test_parent_must_share_run_and_session(tmp_path) -> None:
    store = _store(tmp_path)
    _execution(store)

    child = _execution(
        store,
        execution_id="exec_2",
        parent_execution_id="exec_1",
    )
    assert child.parent_execution_id == "exec_1"

    with pytest.raises(ExecutionConflict) as wrong_run:
        _execution(
            store,
            execution_id="exec_3",
            run_id="run_2",
            parent_execution_id="exec_1",
        )
    assert wrong_run.value.code == "parent_identity_mismatch"


def test_command_idempotency_reuses_exact_request_and_rejects_collision(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    execution = _execution(store)
    command = store.accept_command(
        command_id="cmd_1",
        execution_id=execution.execution_id,
        expected_version=execution.status_version,
        kind=CommandKind.PAUSE,
        payload={"reason": "user"},
        actor={"surface": "web", "subject": "owner"},
    )
    duplicate = store.accept_command(
        command_id="cmd_1",
        execution_id=execution.execution_id,
        expected_version=execution.status_version,
        kind=CommandKind.PAUSE,
        payload={"reason": "user"},
        actor={"surface": "web", "subject": "owner"},
    )
    assert duplicate == command

    with pytest.raises(CommandConflict) as collision:
        store.accept_command(
            command_id="cmd_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            kind=CommandKind.CANCEL,
            payload={"reason": "user"},
            actor={"surface": "web", "subject": "owner"},
        )
    assert collision.value.code == "idempotency_collision"


def test_stale_command_is_rejected_before_it_is_recorded(tmp_path) -> None:
    store = _store(tmp_path)
    execution = _execution(store)
    running = store.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.RUNNING,
    )

    with pytest.raises(ExecutionConflict) as stale:
        store.accept_command(
            command_id="cmd_stale",
            execution_id=running.execution_id,
            expected_version=execution.status_version,
            kind=CommandKind.PAUSE,
            payload={},
            actor={"surface": "cli"},
        )
    assert stale.value.code == "stale_version"
    assert store.get_command("cmd_stale") is None


def test_accept_with_transition_persists_pause_intent_atomically(tmp_path) -> None:
    store = _store(tmp_path)
    execution = _execution(store)
    running = store.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.RUNNING,
    )

    command, pausing, duplicate = store.accept_command_with_transition(
        command_id="cmd_pause",
        execution_id=running.execution_id,
        expected_version=running.status_version,
        kind=CommandKind.PAUSE,
        target=ExecutionStatus.PAUSING,
        payload={"reason": "user"},
        actor={"surface": "web"},
        reason_code="user_pause",
    )

    assert command.status is CommandStatus.APPLYING
    assert not duplicate
    assert pausing.status is ExecutionStatus.PAUSING
    assert pausing.status_version == running.status_version + 1
    assert store.get_command(command.command_id) == command
    assert store.get_execution(pausing.execution_id) == pausing
    assert [event.kind for event in store.list_events(pausing.execution_id)][-3:] == [
        "command.accepted",
        "execution.updated",
        "command.applying",
    ]


def test_command_status_progress_is_monotonic(tmp_path) -> None:
    store = _store(tmp_path)
    execution = _execution(store)
    execution = store.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.RUNNING,
    )
    command = store.accept_command(
        command_id="cmd_steer",
        execution_id=execution.execution_id,
        expected_version=execution.status_version,
        kind=CommandKind.STEER,
        payload={"message": "Use the verified source"},
        actor={"surface": "tui"},
    )
    applying = store.transition_command(
        command.command_id,
        expected_status=CommandStatus.ACCEPTED,
        target=CommandStatus.APPLYING,
    )
    applied = store.transition_command(
        applying.command_id,
        expected_status=CommandStatus.APPLYING,
        target=CommandStatus.APPLIED,
        result_version=execution.status_version,
    )
    assert applied.status is CommandStatus.APPLIED

    with pytest.raises(CommandConflict) as terminal:
        store.transition_command(
            applied.command_id,
            expected_status=CommandStatus.APPLIED,
            target=CommandStatus.REJECTED,
        )
    assert terminal.value.code == "terminal"


def test_list_nonterminal_excludes_every_terminal_status(tmp_path) -> None:
    store = _store(tmp_path)
    active = _execution(store)
    for index, status in enumerate(
        (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.INTERRUPTED,
        ),
        start=2,
    ):
        record = _execution(store, execution_id=f"exec_{index}")
        if status is ExecutionStatus.CANCELLED:
            record = store.transition_execution(
                record.execution_id,
                expected_version=record.status_version,
                target=ExecutionStatus.CANCELLING,
            )
        else:
            record = store.transition_execution(
                record.execution_id,
                expected_version=record.status_version,
                target=ExecutionStatus.RUNNING,
            )
        store.transition_execution(
            record.execution_id,
            expected_version=record.status_version,
            target=status,
        )

    assert store.list_nonterminal(session_id="session_1") == [active]


def test_default_store_follows_the_active_profile_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "profile" / "executions.db"
    monkeypatch.setattr("openprogram.paths.get_execution_db_path", lambda: target)
    _store_for_path.cache_clear()
    try:
        first = default_store()
        second = default_store()
        assert first is second
        assert first.path == target
    finally:
        _store_for_path.cache_clear()
