"""Per-turn file review WS actions: list_turn_files, turn_file_diff,
revert_turn (openprogram.webui.ws_actions.turn_files).

Exercises both paths the module supports:
  * shadow-git backed — the assistant node carries a ``shadow_git``
    stamp, so stats/diffs come from real ``git diff`` output;
  * legacy fallback — no stamp, so counts and diff come from difflib
    over the checkpoint copy vs current disk, flagged ``approximate``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.store.session.session_store import SessionStore
from openprogram.store.shadow_git.store import ShadowGitStore
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.webui.ws_actions import turn_files as tf


class FakeWS:
    """Collects the JSON frames a handler sends."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> SessionStore:
    s = SessionStore(root_path=tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", s, raising=False,
    )
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", s,
        raising=False,
    )
    # See test_finalize_shadow_git: other suites shadow the lazy
    # `openprogram.store.default_store` re-export with a real attribute.
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: s, raising=False,
    )
    return s


def _seed(store: SessionStore, session_id: str, assistant_msg_id: str) -> None:
    store.create_session(session_id, agent_id="main", title="test")
    store.append_message(session_id, {
        "id": "u1", "role": "user", "content": "edit it", "timestamp": 1.0,
    })
    store.append_message(session_id, {
        "id": assistant_msg_id, "role": "assistant", "content": "done",
        "predecessor": "u1", "timestamp": 2.0,
    })


def _stamp_shadow(store: SessionStore, session_id: str, msg_id: str,
                  meta: dict) -> None:
    _git, idx = store._open(session_id)
    node = idx.nodes_by_id[msg_id]
    node.metadata = {**(node.metadata or {}), "shadow_git": meta}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- list

def test_list_turn_files_shape_with_shadow(store, tmp_path, monkeypatch):
    """Shadow-backed turn: every file row carries real +/- counts."""
    session_id, msg_id = "s_list", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "foo.py"
    target.write_text("a\nb\n")

    session_dir = store._session_dir(session_id)
    CheckpointStore(session_dir).backup_before_edit(msg_id, str(target))

    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root):
        shadow = ShadowGitStore(str(project))
        before = shadow.head_sha()
        target.write_text("a\nb\nc\nd\n")
        after = shadow.commit_turn(msg_id, [str(target)], "turn")
        assert after
        _stamp_shadow(store, session_id, msg_id, {
            "repo": str(project), "before": before, "after": after,
        })

        ws = FakeWS()
        _run(tf.handle_list_turn_files(ws, {
            "action": "list_turn_files",
            "session_id": session_id,
            "assistant_msg_id": msg_id,
        }))

    assert ws.sent[0]["type"] == "list_turn_files_result"
    data = ws.sent[0]["data"]
    assert data["paths"] == [str(target)]
    row = data["files"][0]
    assert set(row) == {"path", "rel", "op", "added", "removed"}
    assert row["rel"] == "foo.py"
    assert row["op"] == "modify"
    # Empty-tree baseline: the first shadow commit adds all 4 lines.
    assert row["added"] == 4
    assert row["removed"] == 0


def test_list_turn_files_fallback_counts_without_shadow(store, tmp_path):
    """No shadow stamp: counts still come back, via difflib."""
    session_id, msg_id = "s_fallback", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "bar.py"
    target.write_text("one\ntwo\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("one\ntwo\nthree\n")

    ws = FakeWS()
    _run(tf.handle_list_turn_files(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
    }))

    row = ws.sent[0]["data"]["files"][0]
    assert row["op"] == "modify"
    assert row["added"] == 1
    assert row["removed"] == 0


def test_list_turn_files_marks_created_file_as_add(store, tmp_path):
    session_id, msg_id = "s_add", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "new.py"  # does not exist at turn start
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("fresh\n")

    ws = FakeWS()
    _run(tf.handle_list_turn_files(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
    }))

    row = ws.sent[0]["data"]["files"][0]
    assert row["op"] == "add"
    assert row["added"] == 1


def test_list_turn_files_reports_reverted_turn(store, tmp_path):
    """After an undo, the list says so — that's what keeps the card
    showing "Reverted" instead of offering Undo again after a reload."""
    session_id, msg_id = "s_reverted", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "foo.py"
    target.write_text("original\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("changed\n")
    CheckpointStore(store._session_dir(session_id)).commit_after_edit(
        msg_id, str(target), operation="edit",
    )

    ws = FakeWS()
    _run(tf.handle_list_turn_files(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
    }))
    assert ws.sent[0]["data"]["reverted"] is False

    _run(tf.handle_revert_turn(ws, {
        "session_id": session_id, "msg_id": msg_id,
    }))

    ws2 = FakeWS()
    _run(tf.handle_list_turn_files(ws2, {
        "session_id": session_id, "assistant_msg_id": msg_id,
    }))
    assert ws2.sent[0]["data"]["reverted"] is True


def test_list_turn_files_requires_args(store):
    ws = FakeWS()
    _run(tf.handle_list_turn_files(ws, {"session_id": "", "assistant_msg_id": ""}))
    data = ws.sent[0]["data"]
    assert data["files"] == []
    assert "required" in data["error"]


# ---------------------------------------------------------------- diff

def test_turn_file_diff_uses_shadow(store, tmp_path):
    session_id, msg_id = "s_diff", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "foo.py"
    target.write_text("keep\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))

    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root):
        shadow = ShadowGitStore(str(project))
        before = shadow.head_sha()
        target.write_text("keep\nadded line\n")
        after = shadow.commit_turn(msg_id, [str(target)], "turn")
        _stamp_shadow(store, session_id, msg_id, {
            "repo": str(project), "before": before, "after": after,
        })

        ws = FakeWS()
        _run(tf.handle_turn_file_diff(ws, {
            "session_id": session_id, "assistant_msg_id": msg_id,
            "path": str(target),
        }))

    data = ws.sent[0]["data"]
    assert ws.sent[0]["type"] == "turn_file_diff_result"
    assert data["approximate"] is False
    assert "+added line" in data["diff"]


def test_turn_file_diff_fallback_is_approximate(store, tmp_path):
    session_id, msg_id = "s_diff_fb", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "baz.py"
    target.write_text("old\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("new\n")

    ws = FakeWS()
    _run(tf.handle_turn_file_diff(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "path": str(target),
    }))

    data = ws.sent[0]["data"]
    assert data["approximate"] is True
    assert "-old" in data["diff"]
    assert "+new" in data["diff"]


def test_turn_file_diff_unknown_path_errors(store, tmp_path):
    session_id, msg_id = "s_diff_missing", "u1_reply"
    _seed(store, session_id, msg_id)

    ws = FakeWS()
    _run(tf.handle_turn_file_diff(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "path": str(tmp_path / "never.py"),
    }))
    assert "not recorded" in ws.sent[0]["data"]["error"]


def test_turn_file_diff_requires_args(store):
    ws = FakeWS()
    _run(tf.handle_turn_file_diff(ws, {"session_id": "s", "path": ""}))
    assert "required" in ws.sent[0]["data"]["error"]


# -------------------------------------------------------------- revert

def test_revert_turn_action_restores_and_reports(store, tmp_path):
    session_id, msg_id = "s_revert", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "foo.py"
    target.write_text("original\n")
    CheckpointStore(store._session_dir(session_id)).backup_before_edit(
        msg_id, str(target))
    target.write_text("agent wrote this\n")
    CheckpointStore(store._session_dir(session_id)).commit_after_edit(
        msg_id, str(target), operation="edit",
    )

    ws = FakeWS()
    _run(tf.handle_revert_turn(ws, {
        "session_id": session_id, "msg_id": msg_id,
    }))

    data = ws.sent[0]["data"]
    assert ws.sent[0]["type"] == "revert_turn_result"
    assert str(target) in data["reverted_paths"]
    assert data["errors"] == []
    assert target.read_text() == "original\n"


def test_revert_turn_action_reports_error(store):
    ws = FakeWS()
    _run(tf.handle_revert_turn(ws, {
        "session_id": "nope", "msg_id": "u1_reply",
    }))
    data = ws.sent[0]["data"]
    assert data["reverted_paths"] == []
    assert data["errors"]


def test_actions_registered():
    assert set(tf.ACTIONS) == {
        "list_turn_files", "turn_file_diff", "revert_turn", "reapply_turn",
    }


def test_revert_turn_rejects_active_run(store, monkeypatch):
    from openprogram.webui import server as _server

    monkeypatch.setattr(_server, "_is_run_active", lambda _session_id: True)
    ws = FakeWS()
    _run(tf.handle_revert_turn(ws, {
        "session_id": "busy", "msg_id": "u1_reply",
    }))

    data = ws.sent[0]["data"]
    assert data["status"] == "blocked"
    assert data["reverted_paths"] == []
    assert data["errors"] == ["run_active"]


def test_reapply_turn_restores_after_image(store, tmp_path, monkeypatch):
    from openprogram.webui import server as _server

    monkeypatch.setattr(_server, "_is_run_active", lambda _session_id: False)
    session_id, msg_id = "s_ws_redo", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "redo.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(msg_id, str(target), operation="edit")
    undo_ws = FakeWS()
    _run(tf.handle_revert_turn(undo_ws, {
        "session_id": session_id,
        "msg_id": msg_id,
        "idempotency_key": "undo-ws",
    }))
    assert target.read_text(encoding="utf-8") == "before\n"

    redo_ws = FakeWS()
    _run(tf.handle_reapply_turn(redo_ws, {
        "session_id": session_id,
        "msg_id": msg_id,
        "idempotency_key": "redo-ws",
    }))

    data = redo_ws.sent[0]["data"]
    assert data["status"] == "committed"
    assert data["reapplied_paths"] == [str(target)]
    assert data["errors"] == []
    assert target.read_text(encoding="utf-8") == "after\n"
