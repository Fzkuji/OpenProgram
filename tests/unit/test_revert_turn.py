"""Unit tests for openprogram.agent._revert.revert_turn.

Drives the BackupStore directly (no real dispatcher needed) — the
unit under test is the wiring between SessionStore + BackupStore +
DAG metadata stamping.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent._revert import revert_turn
from openprogram.store.session_store import SessionStore
from openprogram.store.file_backup import BackupStore


@pytest.fixture
def store_with_session(tmp_path: Path, monkeypatch):
    """Build a SessionStore rooted under tmp_path and install it as the
    default_store singleton so revert_turn picks it up."""
    store = SessionStore(root_path=tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session_store._default_store", store,
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
        "parent_id": user_msg_id,
        "timestamp": 2.0,
    })


def test_revert_restores_file_and_stamps_metadata(store_with_session, tmp_path):
    session_id = "s_revert_basic"
    assistant_msg_id = "u1_reply"
    store = store_with_session
    _seed_session(store, session_id, assistant_msg_id)

    # Pre-edit state.
    target = tmp_path / "work" / "foo.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("original")

    # Simulate a turn: snapshot, then mutate.
    session_dir = store._session_dir(session_id)
    backup = BackupStore(session_dir)
    backup.backup_before_edit(assistant_msg_id, str(target))
    target.write_text("agent overwrote it")

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
    # Metadata is still stamped — the UI may want to record "user
    # asked to revert this turn" even when no files were affected.
    assert result["metadata_stamped"] is True
