"""What follows an execution event: the assistant message and client frames."""
from __future__ import annotations

import asyncio

from openprogram.runtime.execution import (
    AttemptStore,
    CapabilitySet,
    ExecutionEventEffects,
    ExecutionStatus,
    ExecutionStore,
    RuntimeControlService,
    start_event_effects,
    stop_event_effects,
)
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.model import CommandStatus
from openprogram.state.store import SessionStore
from tests.support.waiting import wait_until


def _store(tmp_path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")


def _admit(store: ExecutionStore, *, assistant_id=None):
    revision = store.create_revision(revision_id="revision-1", manifest={"entrypoint": "workflow.run"})
    return store.admit_execution(
        execution_id="exec-1", run_id="run-1", session_id="session-1",
        revision_id=revision.revision_id, input_ref="blob:input-1", input_hash="hash-1",
        entrypoint="workflow.run",
        trusted_actor={"subject": "user-1", "session_id": "session-1"},
        config_snapshot_ref="blob:config-1", user_message_id="msg-1",
        assistant_message_id=assistant_id, capabilities=CapabilitySet(pause=True),
    )


def _advance(store, execution, *targets):
    for target in targets:
        execution = store.transition_execution(
            execution.execution_id, expected_version=execution.status_version, target=target,
        )
    return execution


def _sessions(tmp_path, monkeypatch, request):
    sessions = SessionStore(tmp_path / "sessions")
    request.addfinalizer(sessions.close)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: sessions)
    sessions.create_session("session-1", "main", status="running")
    return sessions


def test_each_lifecycle_change_reaches_clients_once_and_in_order(tmp_path, monkeypatch):
    store = _store(tmp_path)
    frames = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    effects = ExecutionEventEffects(store)
    _advance(store, _admit(store), ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED)

    effects.drain()
    effects.drain()

    statuses = [frame["data"]["execution"]["status"] for frame in frames]
    assert statuses == ["queued", "running", "completed"]
    sequences = [frame["data"]["event_sequence"] for frame in frames]
    assert sequences == sorted(sequences)


def test_a_new_reader_starts_after_what_was_already_written(tmp_path, monkeypatch):
    store = _store(tmp_path)
    frames = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    _advance(store, _admit(store), ExecutionStatus.RUNNING)

    assert ExecutionEventEffects(store).drain() == 0
    assert frames == []


def test_running_lists_only_work_in_flight_newest_first(tmp_path):
    store = _store(tmp_path)
    execution = _advance(store, _admit(store), ExecutionStatus.RUNNING)
    effects = ExecutionEventEffects(store)

    [running] = effects.list_running(session_id="session-1")
    assert running.execution_id == execution.execution_id
    assert running.status == "running"
    assert running.payload["ui"] == {"is_running": True, "label": "workflow.run"}
    assert running.payload["input"]["entrypoint"] == "workflow.run"

    _advance(store, execution, ExecutionStatus.COMPLETED)
    assert effects.list_running() == []


def test_the_thread_applies_a_commit_without_waiting_for_its_poll(tmp_path, monkeypatch):
    store = _store(tmp_path)
    frames = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    worker = start_event_effects(store)
    worker.idle_wait_seconds = 60
    worker.wake()
    try:
        assert start_event_effects(store) is worker
        _admit(store)
        assert wait_until(lambda: len(frames) == 1, timeout=10)
    finally:
        assert stop_event_effects(store)
    assert not worker.is_alive


def test_a_cancel_waits_for_a_reply_that_appears_late(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    frames = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    effects = ExecutionEventEffects(store, after=0)
    monkeypatch.setattr("openprogram.runtime.execution.event_effects._RETRY_DELAY_SECONDS", 0.0)
    execution = _admit(store, assistant_id="reply-1")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    result = asyncio.run(control.request_cancel(
        command_id="cancel-late-reply", execution_id=execution.execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        reason_code="cancel.user",
    ))
    assert result.command.status is CommandStatus.APPLIED
    effects.drain()
    assert effects._retries

    sessions.append_message("session-1", {
        "id": "reply-1", "role": "assistant", "content": "partial response", "status": "running",
        "extra": '{"blocks": [{"type": "text", "text": "partial response"}]}',
    })
    head = sessions.get_session("session-1")["head_id"]
    effects.drain()

    assert not effects._retries
    reply = sessions.get_messages("session-1")[-1]
    assert reply["status"] == "cancelled" and reply["content"] == "partial response"
    assert sessions.get_session("session-1")["head_id"] == head
    assert sessions.get_session("session-1")["status"] == "idle"


def test_a_paused_error_is_closed_by_cancelling_and_keeps_its_trace(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda frame: None)
    sessions.append_message("session-1", {
        "id": "reply-1", "role": "assistant", "content": "partial response", "status": "running",
        "extra": '{"blocks": [{"type": "text", "text": "partial response"}]}',
    })
    effects = ExecutionEventEffects(store, after=0)
    execution = _advance(store, _admit(store, assistant_id="reply-1"), ExecutionStatus.RUNNING)
    paused = store.transition_execution(
        execution.execution_id, expected_version=execution.status_version,
        target=ExecutionStatus.PAUSED, reason_code="continuation_contract_mismatch",
    )
    effects.drain()
    before = sessions.get_messages("session-1")[-1]
    assert before["status"] == "error"

    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    asyncio.run(control.request_cancel(
        command_id="cancel-paused-error", execution_id=paused.execution_id,
        expected_version=paused.status_version, actor={"surface": "test"},
        reason_code="cancel.user",
    ))
    effects.drain()

    reply = sessions.get_messages("session-1")[-1]
    assert reply["status"] == "cancelled" and reply["error"] is None
    assert reply["content"] == before["content"] and reply["blocks"] == before["blocks"]


def test_a_stop_while_paused_reaches_the_engine_as_a_run_with_no_turn_left(tmp_path, monkeypatch, request):
    from openprogram.runtime.execution import hooks

    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda frame: None)
    sessions.append_message("session-1", {"id": "reply-1", "role": "assistant", "content": "", "status": "running"})
    closed = []
    monkeypatch.setattr(hooks, "close_ownerless_run", lambda _store, execution: closed.append(execution.status))
    effects = ExecutionEventEffects(store, after=0)
    execution = _advance(store, _admit(store, assistant_id="reply-1"), ExecutionStatus.RUNNING)
    paused = store.transition_execution(
        execution.execution_id, expected_version=execution.status_version,
        target=ExecutionStatus.PAUSED, reason_code="waiting_approval",
    )
    effects.drain()
    assert closed == []
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    asyncio.run(control.request_cancel(
        command_id="cancel-paused", execution_id=paused.execution_id,
        expected_version=paused.status_version, actor={"surface": "test"}, reason_code="cancel.user",
    ))
    effects.drain()
    assert closed == [ExecutionStatus.CANCELLED]


def test_startup_finishes_what_the_previous_worker_left(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    sessions.append_message("session-1", {
        "id": "reply-1", "role": "assistant", "content": "", "status": "running",
    })
    execution = _advance(store, _admit(store, assistant_id="reply-1"), ExecutionStatus.RUNNING)
    store.transition_execution(
        execution.execution_id, expected_version=execution.status_version,
        target=ExecutionStatus.FAILED, reason_code="agent_runner_error",
    )

    assert ExecutionEventEffects(store).catch_up() == 1

    reply = sessions.get_messages("session-1")[-1]
    assert reply["status"] == "error" and reply["error"] == "agent_runner_error"


def _reply(sessions, status="running", **metadata):
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    SessionNodeWriter(sessions, "session-1").append(Call(
        id="assistant-1", role="llm", output="", metadata={"status": status, **metadata},
    ))


def _row(sessions):
    return next(n for n in sessions.get_nodes("session-1") if n.id == "assistant-1").metadata


def test_a_row_left_open_settles_when_its_execution_ends(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda _frame: None)
    _reply(sessions)
    effects = ExecutionEventEffects(store)

    _advance(store, _admit(store, assistant_id="assistant-1"), ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED)
    effects.drain()

    assert _row(sessions)["status"] == "completed"


def test_a_settled_row_is_left_alone(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda _frame: None)
    _reply(sessions, status="error")
    effects = ExecutionEventEffects(store)

    _advance(store, _admit(store, assistant_id="assistant-1"), ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED)
    effects.drain()

    assert _row(sessions)["status"] == "error"


def test_a_pause_and_its_resume_are_written_to_the_row(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda _frame: None)
    _reply(sessions)
    effects = ExecutionEventEffects(store)

    execution = _advance(store, _admit(store, assistant_id="assistant-1"),
                         ExecutionStatus.RUNNING, ExecutionStatus.PAUSING, ExecutionStatus.PAUSED)
    effects.drain()
    assert _row(sessions)["status"] == "paused"

    _advance(store, execution, ExecutionStatus.RUNNING)
    effects.drain()
    row = _row(sessions)
    assert row["status"] == "running" and row.get("pause_reason") is None


def test_an_old_event_replayed_writes_nothing(tmp_path, monkeypatch, request):
    store = _store(tmp_path)
    sessions = _sessions(tmp_path, monkeypatch, request)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", lambda _frame: None)
    _reply(sessions)
    effects = ExecutionEventEffects(store, after=0)
    execution = _advance(store, _admit(store, assistant_id="assistant-1"),
                         ExecutionStatus.RUNNING, ExecutionStatus.PAUSING, ExecutionStatus.PAUSED)
    _advance(store, execution, ExecutionStatus.RUNNING)

    effects.drain()

    assert _row(sessions)["status"] == "running"
    paused = [e for e in store.list_events_after(0, limit=50)
              if store.execution_snapshot_at(e.execution_id, e.sequence).status == ExecutionStatus.PAUSED]
    effects.apply(paused[0], notify_clients=False)
    assert _row(sessions)["status"] == "running"
