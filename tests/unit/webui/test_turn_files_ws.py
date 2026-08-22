"""Exact journal Review scopes, bounded diffs, Undo and Reapply."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from openprogram.store.session.session_store import SessionStore
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


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- list

def test_list_turn_files_uses_committed_journal_not_shadow(store, tmp_path):
    session_id, msg_id = "s_list", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "foo.py"
    target.write_text("a\nb\n")

    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("a\nb\nc\nd\n")
    journal.commit_after_edit(msg_id, str(target), operation="edit")
    _git, index = store._open(session_id)
    index.nodes_by_id[msg_id].metadata = {
        **(index.nodes_by_id[msg_id].metadata or {}),
        "shadow_git": {"repo": str(project), "before": "bad", "after": "bad"},
    }

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
    assert {"path", "rel", "op", "added", "removed"} <= set(row)
    assert row["rel"] == "foo.py"
    assert row["op"] == "modify"
    assert row["added"] == 2
    assert row["removed"] == 0


def test_list_turn_files_reads_exact_journal_stats(store, tmp_path):
    session_id, msg_id = "s_fallback", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "bar.py"
    target.write_text("one\ntwo\n")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("one\ntwo\nthree\n")
    journal.commit_after_edit(msg_id, str(target), operation="edit")

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
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("fresh\n")
    journal.commit_after_edit(msg_id, str(target), operation="add")

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

def test_turn_file_diff_uses_exact_before_and_after_blobs(store, tmp_path):
    session_id, msg_id = "s_diff", "u1_reply"
    _seed(store, session_id, msg_id)

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "foo.py"
    target.write_text("keep\n")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("keep\nadded line\n")
    journal.commit_after_edit(msg_id, str(target), operation="edit")
    target.write_text("later external edit\n")

    ws = FakeWS()
    _run(tf.handle_turn_file_diff(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "path": str(target),
    }))

    data = ws.sent[0]["data"]
    assert ws.sent[0]["type"] == "turn_file_diff_result"
    assert data["approximate"] is False
    assert "+added line" in data["diff"]


def test_turn_file_diff_without_shadow_is_still_exact(store, tmp_path):
    session_id, msg_id = "s_diff_fb", "u1_reply"
    _seed(store, session_id, msg_id)

    target = tmp_path / "baz.py"
    target.write_text("old\n")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("new\n")
    journal.commit_after_edit(msg_id, str(target), operation="edit")

    ws = FakeWS()
    _run(tf.handle_turn_file_diff(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "path": str(target),
    }))

    data = ws.sent[0]["data"]
    assert data["approximate"] is False
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
        "list_turn_files", "turn_file_diff", "review_scope", "review_file_diff",
        "revert_turn", "reapply_turn",
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


def test_turn_scope_uses_summary_embedded_on_history_node(store, tmp_path):
    session_id, msg_id = "s_embedded", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "embedded.py"
    _git, index = store._open(session_id)
    index.nodes_by_id[msg_id].metadata = {
        **(index.nodes_by_id[msg_id].metadata or {}),
        "turn_files": {
            "version": 2,
            "file_count": 1,
            "added": 3,
            "removed": 1,
            "files": [{
                "path": str(target), "op": "modify", "added": 3,
                "removed": 1, "binary": False, "diff_state": "available",
                "recoverability": "exact", "unavailable_reason": None,
            }],
        },
    }

    ws = FakeWS()
    _run(tf.handle_review_scope(ws, {
        "session_id": session_id, "scope": "turn", "assistant_msg_id": msg_id,
    }))

    data = ws.sent[0]["data"]
    assert data["status"] == "ready"
    assert data["source"] == "mutation_journal"
    assert data["file_count"] == 1
    assert data["files"][0]["added"] == 3


def test_branch_scope_excludes_sibling_turn_receipt(store, tmp_path):
    session_id = "s_branch_scope"
    _seed(store, session_id, "a1")
    active = tmp_path / "active.py"
    active.write_text("v0\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit("a1", str(active))
    active.write_text("v1\n", encoding="utf-8")
    journal.commit_after_edit("a1", str(active), operation="edit")
    store.append_message(session_id, {
        "id": "u2", "role": "user", "content": "again", "predecessor": "a1",
    })
    store.append_message(session_id, {
        "id": "a2", "role": "assistant", "content": "done", "predecessor": "u2",
    })
    journal.backup_before_edit("a2", str(active))
    active.write_text("v2\n", encoding="utf-8")
    journal.commit_after_edit("a2", str(active), operation="edit")
    sibling = tmp_path / "sibling.py"
    sibling.write_text("before\n", encoding="utf-8")
    store.append_message(session_id, {
        "id": "fork-u", "role": "user", "content": "fork", "predecessor": "a1",
    })
    store.append_message(session_id, {
        "id": "fork-a", "role": "assistant", "content": "forked",
        "predecessor": "fork-u",
    })
    journal.backup_before_edit("fork-a", str(sibling))
    sibling.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit("fork-a", str(sibling), operation="edit")
    store.set_head(session_id, "a2")

    result = tf._branch_scope(session_id)

    assert result["source"] == "mutation_journal"
    assert [row["path"] for row in result["files"]] == [str(active)]
    assert result["files"][0]["turn_ids"] == ["a1", "a2"]


def test_workspace_scope_excludes_gitignored_files(store, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    tracked = root / "tracked.py"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    tracked.write_text("after\n", encoding="utf-8")
    (root / "visible.tmp").write_text("visible\n", encoding="utf-8")
    (root / "ignored.log").write_text("ignore me\n", encoding="utf-8")
    monkeypatch.setattr(tf, "_project_root", lambda _session_id: root)

    result = tf._workspace_scope("session")

    rels = {row["rel"] for row in result["files"]}
    assert rels == {"tracked.py", "visible.tmp"}
    assert result["source"] == "git"
    assert result["ignored_policy"] == "exclude_standard"


def test_exact_diff_payload_is_bounded(store, tmp_path):
    session_id, msg_id = "s_large_diff", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "large.txt"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("x" * (tf._MAX_DIFF_BYTES + 1), encoding="utf-8")
    journal.commit_after_edit(msg_id, str(target), operation="edit")

    result = tf._turn_file_diff(session_id, msg_id, str(target))

    assert result["diff"] == ""
    assert result["diff_state"] == "large"
