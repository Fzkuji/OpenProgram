from __future__ import annotations

import sqlite3

from openprogram.execution import CapabilitySet, ExecutionStatus, ExecutionStore
from openprogram.execution.outbox import ProjectionDispatcher


def _store(tmp_path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")


def _admit(store: ExecutionStore):
    revision = store.create_revision(
        revision_id="revision-1", manifest={"entrypoint": "workflow.run"}
    )
    return store.admit_execution(
        execution_id="exec-1",
        run_id="run-1",
        session_id="session-1",
        revision_id=revision.revision_id,
        input_ref="blob:input-1",
        input_hash="hash-1",
        entrypoint="workflow.run",
        trusted_actor={"subject": "user-1", "session_id": "session-1"},
        config_snapshot_ref="blob:config-1",
        user_message_id="msg-1",
        capabilities=CapabilitySet(pause=True),
    )


def test_fixed_consumers_materialize_idempotent_projection_history_and_running_view(
    tmp_path, monkeypatch
):
    from openprogram.execution.projections import (
        ExecutionProjectionReadModel,
        projection_handlers,
    )

    store = _store(tmp_path)
    execution = _admit(store)
    model = ExecutionProjectionReadModel(store)
    dispatcher = ProjectionDispatcher(store, projection_handlers(store))
    frames = []
    monkeypatch.setattr("openprogram.events.emit_ws_frame", frames.append)

    assert dispatcher.dispatch_once(owner_id="projection-worker").delivered == 4
    assert frames[0]["type"] == "execution.updated"
    assert frames[0]["data"]["execution"]["execution_id"] == execution.execution_id
    first = model.get_current("ui", execution.execution_id)
    assert first is not None
    assert first.payload["execution"]["status"] == "queued"
    assert first.payload["ui"]["is_running"] is True
    assert model.list_running(session_id="session-1") == [first]

    # Replaying a delivered payload is harmless: the read model keeps one
    # immutable history row and one latest snapshot for this event.
    dag_item = next(
        item for item in store.list_projection_outbox(execution_id=execution.execution_id)
        if item.projection_kind == "dag"
    )
    projection_handlers(store)["dag"](dag_item)
    assert len(model.list_events("dag", execution.execution_id)) == 1

    running = store.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.RUNNING,
    )
    store.transition_execution(
        execution.execution_id,
        expected_version=running.status_version,
        target=ExecutionStatus.COMPLETED,
    )
    assert dispatcher.dispatch_once(owner_id="projection-worker").delivered == 8

    ui = model.get_current("ui", execution.execution_id)
    assert ui is not None
    assert ui.payload["execution"]["status"] == "completed"
    assert ui.payload["ui"]["is_running"] is False
    assert model.list_running(session_id="session-1") == []
    assert [event.event_sequence for event in model.list_events("ui", execution.execution_id)] == sorted(
        event.event_sequence for event in model.list_events("ui", execution.execution_id)
    )


def test_consumer_failure_leaves_projection_pending_without_changing_execution(tmp_path):
    from openprogram.execution.projections import projection_handlers

    store = _store(tmp_path)
    execution = _admit(store)
    handlers = projection_handlers(store)

    def fail(_item):
        raise RuntimeError("read model unavailable")

    handlers["dag"] = fail
    result = ProjectionDispatcher(store, handlers).dispatch_once(owner_id="projection-worker")

    assert result.failed == 1
    assert store.get_execution(execution.execution_id) == execution
    dag = next(
        item for item in store.list_projection_outbox(execution_id=execution.execution_id)
        if item.projection_kind == "dag"
    )
    assert dag.state.value == "pending"


def test_fixed_handlers_cover_every_declared_projection_kind(tmp_path):
    from openprogram.execution._schema import PROJECTION_KINDS
    from openprogram.execution.projections import projection_handlers

    assert set(projection_handlers(_store(tmp_path))) == set(PROJECTION_KINDS)


def test_default_startup_registers_and_replays_fixed_consumers(tmp_path, monkeypatch):
    from openprogram.execution import recover_execution_startup
    from openprogram.execution.projections import ExecutionProjectionReadModel

    store = _store(tmp_path)
    _admit(store)
    monkeypatch.setattr("openprogram.execution.store.default_store", lambda: store)

    class Control:
        def recover_startup(self):
            return ()

    result = recover_execution_startup(
        control_service=Control(), projection_owner_id="startup-worker"
    )
    assert result.projections.delivered == 4
    assert ExecutionProjectionReadModel(store).get_current("ui", "exec-1") is not None


def test_v5_migration_requeues_fixed_projections_for_the_new_read_models(tmp_path):
    from openprogram.execution._schema import SCHEMA_VERSION
    from openprogram.execution.outbox import ProjectionOutboxState

    path = tmp_path / "runtime" / "executions.sqlite3"
    store = ExecutionStore(path)
    _admit(store)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_projection_current")
        connection.execute("DROP TABLE execution_projection_events")
        connection.execute(
            "UPDATE execution_projection_outbox SET state = 'delivered', delivered_at = 1"
        )
        connection.execute("PRAGMA user_version = 5")
        connection.commit()

    migrated = ExecutionStore(path)
    assert {
        item.state
        for item in migrated.list_projection_outbox(execution_id="exec-1")
    } == {ProjectionOutboxState.PENDING}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"execution_projection_current", "execution_projection_events"}.issubset(tables)
