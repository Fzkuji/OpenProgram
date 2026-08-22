"""Unit tests for openprogram.agent.internals._revert.revert_turn.

Drives the CheckpointStore directly (no real dispatcher needed) — the
unit under test is the wiring between SessionStore + CheckpointStore +
DAG metadata stamping.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from openprogram.agent.internals._revert import reapply_turn, revert_turn
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def store_with_session(tmp_path: Path, monkeypatch):
    """Build a SessionStore rooted under tmp_path and install it as the
    default_store singleton so revert_turn picks it up."""
    store = SessionStore(root_path=tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", store,
        raising=False,
    )
    return store


def _seed_session(store: SessionStore, session_id: str, assistant_msg_id: str,
                  user_msg_id: str = "u1") -> None:
    """Create a session with a user msg + assistant placeholder so the
    DAG has a node we can stamp ``reverted`` on."""
    store.create_session(session_id, agent_id="main", title="test")
    store.append_message(session_id, {
        "id": user_msg_id,
        "role": "user",
        "content": "edit foo.py please",
        "timestamp": 1.0,
    })
    store.append_message(session_id, {
        "id": assistant_msg_id,
        "role": "assistant",
        "content": "ok done",
        "predecessor": user_msg_id,
        "timestamp": 2.0,
    })


def _record_mutation(
    store: SessionStore,
    session_id: str,
    turn_id: str,
    target: Path,
    after: str,
) -> CheckpointStore:
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(turn_id, str(target))
    target.write_text(after, encoding="utf-8")
    journal.commit_after_edit(turn_id, str(target), operation="edit")
    return journal


def test_revert_restores_file_and_stamps_metadata(store_with_session, tmp_path):
    session_id = "s_revert_basic"
    assistant_msg_id = "u1_reply"
    store = store_with_session
    _seed_session(store, session_id, assistant_msg_id)

    # Pre-edit state.
    target = tmp_path / "work" / "foo.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("original")

    # Simulate a turn: back up, then mutate.
    session_dir = store._session_dir(session_id)
    backup = CheckpointStore(session_dir)
    backup.backup_before_edit(assistant_msg_id, str(target))
    target.write_text("agent overwrote it")
    backup.commit_after_edit(assistant_msg_id, str(target), operation="edit")

    # Revert.
    result = revert_turn(session_id, assistant_msg_id)

    assert not result.get("error")
    assert str(target) in result["restored_paths"]
    assert target.read_text() == "original"
    assert result["metadata_stamped"] is True

    # DAG node was stamped.
    pair = store._open(session_id)
    assert pair is not None
    _git, idx = pair
    node = idx.nodes_by_id.get(assistant_msg_id)
    assert node is not None
    assert (node.metadata or {}).get("reverted") is True
    assert (node.metadata or {}).get("reverted_at") is not None
    assert str(target) in (node.metadata or {}).get("reverted_paths", [])


def test_revert_unknown_session_returns_error(store_with_session):
    result = revert_turn("nope", "u1_reply")
    assert result["restored_paths"] == []
    assert "unknown session" in (result.get("error") or "")


def test_revert_missing_args_returns_error(store_with_session):
    result = revert_turn("", "")
    assert result["restored_paths"] == []
    assert "required" in (result.get("error") or "")


def test_revert_with_no_backed_files_is_noop(store_with_session):
    session_id = "s_revert_empty"
    assistant_msg_id = "u9_reply"
    _seed_session(store_with_session, session_id, assistant_msg_id, user_msg_id="u9")
    result = revert_turn(session_id, assistant_msg_id)
    assert result["restored_paths"] == []
    assert result["metadata_stamped"] is False
    assert "no committed mutations" in result["error"]


def test_revert_blocks_later_file_change_without_writing(store_with_session, tmp_path):
    session_id, turn_id = "s_conflict", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    target = tmp_path / "conflict.py"
    target.write_text("before\n", encoding="utf-8")
    _record_mutation(store_with_session, session_id, turn_id, target, "agent\n")
    target.write_text("user later\n", encoding="utf-8")

    result = revert_turn(session_id, turn_id)

    assert result["status"] == "blocked"
    assert result["restored_paths"] == []
    assert result["conflicts"] == [str(target)]
    assert target.read_text(encoding="utf-8") == "user later\n"
    assert result["metadata_stamped"] is False


def test_revert_does_not_delete_changed_created_file(store_with_session, tmp_path):
    session_id, turn_id = "s_created_conflict", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    target = tmp_path / "created.py"
    journal = CheckpointStore(store_with_session._session_dir(session_id))
    journal.backup_before_edit(turn_id, str(target))
    target.write_text("agent created\n", encoding="utf-8")
    journal.commit_after_edit(turn_id, str(target), operation="add")
    target.write_text("user replaced\n", encoding="utf-8")

    result = revert_turn(session_id, turn_id)

    assert result["status"] == "blocked"
    assert result["restored_paths"] == []
    assert target.read_text(encoding="utf-8") == "user replaced\n"


def test_mid_apply_failure_rolls_back_every_file(
    store_with_session, tmp_path, monkeypatch,
):
    session_id, turn_id = "s_rollback", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first before\n", encoding="utf-8")
    second.write_text("second before\n", encoding="utf-8")
    _record_mutation(store_with_session, session_id, turn_id, first, "first after\n")
    journal = _record_mutation(
        store_with_session, session_id, turn_id, second, "second after\n",
    )
    original_apply = CheckpointStore._apply_state
    failed = {"value": False}

    def fail_second_once(self, path, state, backup_dir, transaction_id):
        if Path(path) == second and not failed["value"]:
            failed["value"] = True
            raise OSError("injected second-file failure")
        return original_apply(self, path, state, backup_dir, transaction_id)

    monkeypatch.setattr(CheckpointStore, "_apply_state", fail_second_once)

    result = revert_turn(session_id, turn_id)

    assert result["status"] == "rolled_back"
    assert result["restored_paths"] == []
    assert first.read_text(encoding="utf-8") == "first after\n"
    assert second.read_text(encoding="utf-8") == "second after\n"
    assert result["metadata_stamped"] is False
    assert journal.list_mutations(turn_id)


def test_revert_is_idempotent_and_reapply_restores_after_image(
    store_with_session, tmp_path,
):
    session_id, turn_id = "s_reapply", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    target = tmp_path / "redo.py"
    target.write_text("before\n", encoding="utf-8")
    _record_mutation(store_with_session, session_id, turn_id, target, "after\n")

    first = revert_turn(session_id, turn_id, idempotency_key="undo-1")
    repeated = revert_turn(session_id, turn_id, idempotency_key="undo-1")
    redone = reapply_turn(session_id, turn_id, idempotency_key="redo-1")

    assert first["status"] == "committed"
    assert repeated["transaction_id"] == first["transaction_id"]
    assert target.read_text(encoding="utf-8") == "after\n"
    assert redone["status"] == "committed"
    assert redone["restored_paths"] == [str(target)]
    _git, index = store_with_session._open(session_id)
    assert index.nodes_by_id[turn_id].metadata["reverted"] is False


def test_missing_rollback_blob_is_unavailable_before_any_write(
    store_with_session, tmp_path,
):
    session_id, turn_id = "s_missing_rollback", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first before\n", encoding="utf-8")
    second.write_text("second before\n", encoding="utf-8")
    journal = _record_mutation(
        store_with_session, session_id, turn_id, first, "first after\n",
    )
    _record_mutation(
        store_with_session, session_id, turn_id, second, "second after\n",
    )
    first_mutation = journal.list_mutations(turn_id)[0]
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir
    missing = turn_backup_dir(
        store_with_session._session_dir(session_id), turn_id,
    ) / first_mutation["after"]["blob_ref"]
    missing.unlink()

    result = revert_turn(session_id, turn_id)

    assert result["status"] == "unavailable"
    assert result["restored_paths"] == []
    assert first.read_text(encoding="utf-8") == "first after\n"
    assert second.read_text(encoding="utf-8") == "second after\n"


def test_history_lock_is_stable_for_overlapping_action_sets(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    first_store = CheckpointStore(tmp_path / "sessions" / "one")
    second_store = CheckpointStore(tmp_path / "sessions" / "two")
    path = str(tmp_path / "workspace" / "a" / "x.py")
    other = str(tmp_path / "workspace" / "b" / "y.py")
    assert first_store._workspace_lock_path() == second_store._workspace_lock_path()
    assert first_store._workspace_lock_path().name == "history.lock"
    assert path != other  # action-set shape does not enter lock identity


def test_plan_change_after_prepare_aborts_without_writing(
    store_with_session, tmp_path, monkeypatch,
):
    session_id, turn_id = "s_stale_plan", "u1_reply"
    _seed_session(store_with_session, session_id, turn_id)
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first before\n", encoding="utf-8")
    second.write_text("second before\n", encoding="utf-8")
    journal = _record_mutation(
        store_with_session, session_id, turn_id, first, "first after\n",
    )
    original_lock = journal._workspace_lock

    @contextmanager
    def add_mutation_before_replan(paths):
        with original_lock(paths):
            _record_mutation(
                store_with_session,
                session_id,
                turn_id,
                second,
                "second after\n",
            )
            yield

    monkeypatch.setattr(journal, "_workspace_lock", add_mutation_before_replan)

    result = journal.apply_history_operation(
        turn_id, "revert", idempotency_key="stale-plan",
    )

    assert result["status"] == "aborted"
    assert result["error"] == "stale_plan"
    assert result["restored_paths"] == []
    assert first.read_text(encoding="utf-8") == "first after\n"
    assert second.read_text(encoding="utf-8") == "second after\n"
