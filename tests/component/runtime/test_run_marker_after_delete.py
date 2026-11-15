"""A deleted conversation leaves no run marker behind.

The marker ``<state>/active/<session>`` tells the next start which
conversations to read for unfinished work. Deleting a conversation cancels
what runs in it, and a turn another process owns stays ``cancelling``: its
owner is the one that ends it. The marker must still go with the
conversation, must not come back, and one left by an older build is dropped
at the next start.
"""

from __future__ import annotations

import pytest

from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.model import CapabilitySet, ExecutionStatus
from openprogram.runtime.execution.store import default_store as default_execution_store
from openprogram.runtime.execution.store.run_tables import RunTables, SessionRunLog

SESSION = "local_deleted"
OTHER = "local_kept"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    from openprogram.runtime import lower_layers
    from openprogram.state.store.session import default_store, session_store

    monkeypatch.setattr(session_store.shared, "_default_store", None)
    lower_layers.register()
    sessions = default_store()
    executions = default_execution_store()
    assert isinstance(executions.tables.log, SessionRunLog)
    return state, sessions, executions


def _running_turn(executions, session_id, execution_id):
    revision = executions.create_revision(manifest={"entrypoint": "turn"})
    execution = executions.create_execution(
        execution_id=execution_id, run_id=f"run_{execution_id}", session_id=session_id,
        revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version, owner_id="another-process")
    _attempt, running = attempts.activate(
        leased.attempt_id, generation=leased.generation, expected_execution_version=reserved.status_version)
    return running


def _cancelled_turn_with_a_sub_agent(sessions, executions):
    """A turn another process owns, asked to stop, whose sub-agent job has ended."""
    sessions.create_session(SESSION, "main")
    sessions.append_message(SESSION, {"id": "u1", "role": "user", "content": "hi"})
    parent = _running_turn(executions, SESSION, "exec_parent")
    revision = executions.create_revision(manifest={"entrypoint": "job"})
    job = executions.create_execution(
        execution_id="j_sub_agent", run_id=parent.run_id, session_id=SESSION,
        revision_id=revision.revision_id, parent_execution_id=parent.execution_id,
    )
    stopping = executions.transition_execution(
        job.execution_id, expected_version=job.status_version, target=ExecutionStatus.CANCELLING)
    executions.transition_execution(
        stopping.execution_id, expected_version=stopping.status_version, target=ExecutionStatus.CANCELLED)
    return executions.transition_execution(
        parent.execution_id, expected_version=parent.status_version,
        target=ExecutionStatus.CANCELLING, reason_code="cancel.session")


def test_deleting_a_conversation_with_a_cancelling_turn_removes_its_marker(profile):
    state, sessions, executions = profile
    _cancelled_turn_with_a_sub_agent(sessions, executions)
    assert (state / "active" / SESSION).exists()

    sessions.delete_session(SESSION)

    assert not (state / "active" / SESSION).exists()
    assert executions.tables.log.active_sessions() == []


def test_a_process_still_holding_the_deleted_conversations_turn_writes_no_marker(profile):
    state, sessions, executions = profile
    _cancelled_turn_with_a_sub_agent(sessions, executions)
    owner_process = RunTables(SessionRunLog(state))
    assert owner_process.handle.get("executions", "exec_parent")["status"] == "cancelling"

    sessions.delete_session(SESSION)
    owner_process._update_active({SESSION})  # noqa: SLF001 - what its next change runs

    assert not (state / "active" / SESSION).exists()


def test_a_start_drops_the_marker_of_a_deleted_conversation_and_keeps_a_running_one(profile):
    state, sessions, executions = profile
    _cancelled_turn_with_a_sub_agent(sessions, executions)
    sessions.create_session(OTHER, "main")
    sessions.append_message(OTHER, {"id": "u1", "role": "user", "content": "hi"})
    _running_turn(executions, OTHER, "exec_kept")
    sessions.delete_session(SESSION)
    # What a build without the fix left behind.
    (state / "active" / SESSION).touch()

    started = RunTables(SessionRunLog(state))

    assert sorted(started.log.active_sessions()) == [OTHER]
    assert started.handle.get("executions", "exec_kept")["status"] == "running"
    assert started.handle.rows("executions", session_id=SESSION) == []
