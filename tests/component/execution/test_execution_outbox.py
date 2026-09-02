from __future__ import annotations

import sqlite3
import time

import pytest

from openprogram.execution import CapabilitySet, ExecutionStore
from openprogram.execution._schema import PROJECTION_KINDS, SCHEMA_VERSION
from openprogram.execution.outbox import (
    ProjectionDispatcher,
    ProjectionOutboxState,
)
from openprogram.execution.startup import recover_execution_startup
from openprogram.execution.store import ExecutionConflict, ProjectionConflict


def _store(tmp_path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")


def _admit(store: ExecutionStore, **overrides):
    values = {
        "execution_id": "exec_1",
        "run_id": "run_1",
        "session_id": "session_1",
        "revision_id": "revision_1",
        "input_ref": "blob:input-1",
        "input_hash": "hash-1",
        "entrypoint": "openprogram.programs.workflow:run",
        "trusted_actor": {"subject": "user-1", "session_id": "session_1"},
        "config_snapshot_ref": "blob:config-1",
        "user_message_id": "msg-1",
        "assistant_message_id": "msg-2",
        "capabilities": CapabilitySet(pause=True),
    }
    values.update(overrides)
    revision = store.create_revision(
        revision_id=values.pop("revision_id"), manifest={"entrypoint": values["entrypoint"]}
    )
    values["revision_id"] = revision.revision_id
    return store.admit_execution(**values)


def test_admission_atomically_persists_immutable_input_event_and_all_projections(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store)

    assert execution.status.value == "queued"
    stored = store.get_execution_input(execution.execution_id)
    assert stored is not None
    assert stored.input_ref == "blob:input-1"
    assert stored.trusted_actor == {"subject": "user-1", "session_id": "session_1"}

    events = store.list_events(execution.execution_id)
    assert [event.kind for event in events] == ["execution.created"]
    entries = store.list_projection_outbox(execution_id=execution.execution_id)
    assert {item.projection_kind for item in entries} == set(PROJECTION_KINDS)
    assert all(item.event_sequence == events[0].sequence for item in entries)
    assert all(item.state is ProjectionOutboxState.PENDING for item in entries)
    assert len(
        store.list_projection_outbox(
            execution_id=execution.execution_id,
            states=(ProjectionOutboxState.PENDING,),
        )
    ) == len(PROJECTION_KINDS)

    # The caller's nested objects cannot alter the durable admission record.
    actor = {"subject": "user-1", "claims": {"role": "member"}}
    admitted = _admit(store, execution_id="exec-2", trusted_actor=actor)
    actor["claims"]["role"] = "admin"
    assert store.get_execution_input(admitted.execution_id).trusted_actor["claims"]["role"] == "member"


def test_admission_failure_rolls_back_execution_input_and_event(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ExecutionConflict, match="input_hash"):
        _admit(store, input_hash="")

    assert store.get_execution("exec_1") is None
    assert store.get_execution_input("exec_1") is None

    original = store._append_event

    def fail_event(*args, **kwargs):
        raise RuntimeError("event write failed")

    store._append_event = fail_event
    with pytest.raises(RuntimeError, match="event write failed"):
        _admit(store, execution_id="exec-rollback")
    store._append_event = original
    assert store.get_execution("exec-rollback") is None
    assert store.get_execution_input("exec-rollback") is None


def test_projection_claim_ack_fail_and_reclaim_are_lease_fenced(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store)
    claimed = store.claim_projection_outbox(owner_id="worker-1", limit=2, lease_ttl_seconds=30)
    assert len(claimed) == 2
    assert all(item.state is ProjectionOutboxState.CLAIMED for item in claimed)
    with pytest.raises(ProjectionConflict) as wrong_owner:
        store.ack_projection_outbox(claimed[0].outbox_id, owner_id="worker-2")
    assert wrong_owner.value.code == "claim_owner_mismatch"

    delivered = store.ack_projection_outbox(claimed[0].outbox_id, owner_id="worker-1")
    assert delivered.state is ProjectionOutboxState.DELIVERED
    failed = store.fail_projection_outbox(
        claimed[1].outbox_id, owner_id="worker-1", error="projection unavailable"
    )
    assert failed.state is ProjectionOutboxState.PENDING
    assert failed.last_error == "projection unavailable"
    assert failed.attempts == 1

    old_claim = store.claim_projection_outbox(owner_id="worker-3", limit=1, lease_ttl_seconds=1)
    assert old_claim
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE execution_projection_outbox SET claim_expires_at = ? WHERE outbox_id = ?",
            (time.time() - 1, old_claim[0].outbox_id),
        )
    assert store.reclaim_projection_outbox() == 1
    assert store.get_projection_outbox(old_claim[0].outbox_id).state is ProjectionOutboxState.PENDING
    assert store.get_execution(execution.execution_id) == execution


def test_expired_projection_owner_cannot_ack_after_reclaim(tmp_path):
    store = _store(tmp_path)
    _admit(store)
    claimed = store.claim_projection_outbox(
        owner_id="worker-1", limit=1, lease_ttl_seconds=1
    )[0]
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE execution_projection_outbox SET claim_expires_at = ? WHERE outbox_id = ?",
            (time.time() - 1, claimed.outbox_id),
        )
    with pytest.raises(ProjectionConflict) as expired:
        store.ack_projection_outbox(claimed.outbox_id, owner_id="worker-1")
    assert expired.value.code == "claim_expired"


def test_partial_consumer_claims_only_registered_kinds(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store)
    claimed = store.claim_projection_outbox(
        owner_id="dag-worker", limit=10, allowed_kinds=("dag",)
    )
    assert [item.projection_kind for item in claimed] == ["dag"]
    entries = store.list_projection_outbox(execution_id=execution.execution_id)
    attempts = {item.projection_kind: item.attempts for item in entries}
    assert attempts["dag"] == 1
    assert attempts["job"] == attempts["workflow"] == attempts["ui"] == 0


def test_expired_handler_claim_is_lost_without_dispatcher_exception(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store)

    def slow_handler(_item):
        time.sleep(0.02)

    result = ProjectionDispatcher(store, {"dag": slow_handler}).dispatch_once(
        owner_id="slow-worker", limit=1, lease_ttl_seconds=0.001
    )
    assert result.claimed == 1
    assert result.delivered == 0
    assert result.failed == 1
    item = next(
        item
        for item in store.list_projection_outbox(execution_id=execution.execution_id)
        if item.projection_kind == "dag"
    )
    assert item.state is ProjectionOutboxState.CLAIMED
    assert store.reclaim_projection_outbox(now=time.time() + 1) == 1


def test_dispatcher_replays_claimed_rows_and_projection_failure_does_not_change_canonical_state(tmp_path):
    store = _store(tmp_path)
    execution = _admit(store)
    seen = []

    def handle(item):
        seen.append(item.projection_kind)
        if item.projection_kind == "job":
            raise RuntimeError("job projection failed")

    result = ProjectionDispatcher(store, {kind: handle for kind in PROJECTION_KINDS}).dispatch_once(
        owner_id="projection-worker", limit=10
    )
    assert result.claimed == 4
    assert result.delivered == 3
    assert result.failed == 1
    assert set(seen) == set(PROJECTION_KINDS)
    assert store.get_execution(execution.execution_id) == execution
    states = {
        item.projection_kind: item.state
        for item in store.list_projection_outbox(execution_id=execution.execution_id)
    }
    assert states["job"] is ProjectionOutboxState.PENDING
    assert states["dag"] is ProjectionOutboxState.DELIVERED


def test_dispatcher_startup_recovery_reclaims_then_replays(tmp_path):
    store = _store(tmp_path)
    _admit(store)
    first = store.claim_projection_outbox(
        owner_id="dead-worker", limit=1, lease_ttl_seconds=1
    )[0]
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE execution_projection_outbox SET claim_expires_at = ? WHERE outbox_id = ?",
            (time.time() - 1, first.outbox_id),
        )
    seen = []
    dispatcher = ProjectionDispatcher(
        store, {kind: lambda item: seen.append(item.outbox_id) for kind in PROJECTION_KINDS}
    )
    result = dispatcher.recover_startup(owner_id="new-worker", limit=1)
    assert result.claimed == 1
    assert result.delivered == 1
    assert seen == [first.outbox_id]


def test_startup_entrypoint_recovers_canonical_before_projection_replay():
    calls = []

    class Control:
        def recover_startup(self):
            calls.append("canonical")
            return ("recovered",)

    class Dispatcher:
        def recover_startup(self, *, owner_id):
            calls.append("projection")
            assert owner_id == "startup-worker"
            return type("Result", (), {"claimed": 0, "delivered": 0, "failed": 0})()

    result = recover_execution_startup(
        control_service=Control(),
        projection_dispatcher=Dispatcher(),
        projection_owner_id="startup-worker",
    )
    assert calls == ["canonical", "projection"]
    assert result.canonical == ("recovered",)


def test_schema_migrates_v4_to_current(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = ExecutionStore(path)
    _admit(store)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_projection_outbox")
        connection.execute("DROP TABLE execution_inputs")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    ExecutionStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert SCHEMA_VERSION == 6
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        outbox_count = connection.execute(
            "SELECT COUNT(*) FROM execution_projection_outbox"
        ).fetchone()[0]
    assert {"execution_inputs", "execution_projection_outbox"}.issubset(tables)
    assert outbox_count == len(PROJECTION_KINDS)


def test_v4_migration_rolls_back_tables_and_version_on_backfill_failure(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    store = ExecutionStore(path)
    _admit(store)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_projection_outbox")
        connection.execute("DROP TABLE execution_inputs")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    from openprogram.execution import _schema

    def fail_backfill(connection):
        raise RuntimeError("backfill failed")

    monkeypatch.setattr(_schema, "_backfill_projection_outbox", fail_backfill)
    with pytest.raises(RuntimeError, match="backfill failed"):
        ExecutionStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "execution_projection_outbox" not in tables
    assert "execution_inputs" not in tables
