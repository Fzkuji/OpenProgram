"""Exact journal Review scopes, bounded diffs, Undo and Reapply."""
from __future__ import annotations

import asyncio
import hashlib
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
        "turn_history_state", "revert_turn", "reapply_turn",
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


def test_workspace_diff_rejects_ignored_path_not_in_scope(store, tmp_path, monkeypatch):
    root = tmp_path / "repo-secret"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    (root / ".gitignore").write_text("*.env\n", encoding="utf-8")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    secret = root / "secret.env"
    secret.write_text("SECRET=hidden\n", encoding="utf-8")
    monkeypatch.setattr(tf, "_project_root", lambda _session_id: root)
    scope = tf._workspace_scope("session")

    result = tf._workspace_file_diff(
        "session", str(secret), scope["snapshot_id"],
    )

    assert result["diff"] == ""
    assert "not in workspace scope" in result["error"]
    ws = FakeWS()
    _run(tf.handle_review_file_diff(ws, {
        "session_id": "session", "scope": "workspace",
        "path": str(secret), "snapshot_id": scope["snapshot_id"],
        "request_id": "ignored-probe",
    }))
    data = ws.sent[0]["data"]
    assert data["request_id"] == "ignored-probe"
    assert data["diff"] == ""
    assert "SECRET=hidden" not in json.dumps(data)
    visible_link = root / "visible-link"
    visible_link.symlink_to(secret)
    linked_scope = tf._workspace_scope("session")
    link_result = tf._workspace_file_diff(
        "session", str(visible_link), linked_scope["snapshot_id"],
    )
    direct_secret = tf._workspace_file_diff(
        "session", str(secret), linked_scope["snapshot_id"],
    )
    assert link_result["diff"] == ""
    assert "not a regular file" in link_result["error"]
    assert direct_secret["diff"] == ""
    assert "SECRET=hidden" not in json.dumps([link_result, direct_secret])


def test_turn_diff_rejects_escaping_or_corrupt_blob(store, tmp_path):
    from openprogram.store.snapshot.checkpoint import manifest
    from openprogram.store.snapshot.checkpoint.paths import (
        turn_backup_dir,
        turn_manifest_path,
    )

    session_id, msg_id = "s_bad_blob", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "safe.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(msg_id, str(target), operation="edit")
    manifest_path = turn_manifest_path(store._session_dir(session_id), msg_id)
    value = manifest.load(manifest_path)
    entry = next(iter(value["files"].values()))
    original_ref = entry["after"]["blob_ref"]
    entry["after"]["blob_ref"] = "../../outside.txt"
    manifest.save(manifest_path, value)
    escaped = tf._turn_file_diff(session_id, msg_id, str(target))
    assert escaped["diff_state"] == "unavailable"
    assert "unsafe recovery blob" in escaped["error"]

    value = manifest.load(manifest_path)
    entry = next(iter(value["files"].values()))
    entry["after"]["blob_ref"] = original_ref
    manifest.save(manifest_path, value)
    (turn_backup_dir(store._session_dir(session_id), msg_id) / original_ref).write_text(
        "tampered\n", encoding="utf-8",
    )
    corrupt = tf._turn_file_diff(session_id, msg_id, str(target))
    assert corrupt["diff_state"] == "unavailable"
    assert "mismatch" in corrupt["error"]
    unsafe_turn = tf._turn_file_diff(
        session_id, "../../../outside", str(target),
    )
    assert unsafe_turn["diff_state"] == "unavailable"
    assert "unsafe turn" in unsafe_turn["error"]


def test_branch_scope_reports_net_zero_as_no_change(store, tmp_path):
    session_id = "s_net_zero"
    _seed(store, session_id, "a1")
    target = tmp_path / "cycle.py"
    target.write_text("a\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit("a1", str(target))
    target.write_text("b\n", encoding="utf-8")
    journal.commit_after_edit("a1", str(target), operation="edit")
    store.append_message(session_id, {
        "id": "u2", "role": "user", "content": "restore", "predecessor": "a1",
    })
    store.append_message(session_id, {
        "id": "a2", "role": "assistant", "content": "done", "predecessor": "u2",
    })
    journal.backup_before_edit("a2", str(target))
    target.write_text("a\n", encoding="utf-8")
    journal.commit_after_edit("a2", str(target), operation="edit")

    result = tf._branch_scope(session_id)

    assert result["files"] == []
    assert result["added"] == 0
    assert result["removed"] == 0


def test_workspace_scope_preserves_rename_identity(store, tmp_path, monkeypatch):
    root = tmp_path / "repo-rename"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    (root / "old.txt").write_text("same\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(root), "mv", "old.txt", "new.txt"], check=True)
    monkeypatch.setattr(tf, "_project_root", lambda _session_id: root)

    result = tf._workspace_scope("session")

    assert result["files"][0]["op"] == "rename"
    assert result["files"][0]["old_rel"] == "old.txt"
    assert result["files"][0]["rel"] == "new.txt"
    assert result["files"][0]["added"] == 0
    assert result["files"][0]["removed"] == 0


def test_review_scope_pages_file_rows_and_echoes_request_id(store, tmp_path):
    session_id, msg_id = "s_scope_page", "u1_reply"
    _seed(store, session_id, msg_id)
    _git, index = store._open(session_id)
    rows = [{
        "path": str(tmp_path / f"f{number}.py"), "op": "modify",
        "added": 1, "removed": 0, "binary": False,
        "diff_state": "available", "recoverability": "exact",
    } for number in range(10_000)]
    index.nodes_by_id[msg_id].metadata = {
        **(index.nodes_by_id[msg_id].metadata or {}),
        "turn_files": {
            "version": 2, "files": rows, "file_count": 10_000,
            "added": 10_000, "removed": 0,
        },
    }
    ws = FakeWS()
    _run(tf.handle_review_scope(ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "scope": "turn", "cursor": 0, "limit": 100,
        "request_id": "scope-request",
    }))

    data = ws.sent[0]["data"]
    assert data["request_id"] == "scope-request"
    assert len(data["files"]) == 100
    assert data["next_cursor"] == 100
    assert data["file_count"] == 10_000
    assert len(json.dumps(data)) < 100_000


def test_review_scope_filters_before_paging_and_invalidates_filter_snapshot(store, tmp_path):
    session_id, msg_id = "s_filtered_scope", "u1_reply"
    _seed(store, session_id, msg_id)
    _git, index = store._open(session_id)
    rows = []
    for number in range(240):
        if number % 3 == 0:
            rel = f"tests/test_{number}.py"
        elif number % 3 == 1:
            rel = f"docs/guide_{number}.md"
        else:
            rel = f"src/module_{number}.py"
        rows.append({
            "path": str(tmp_path / rel), "rel": rel, "op": "modify",
            "added": number, "removed": 1, "binary": False,
            "diff_state": "available", "recoverability": "exact",
        })
    index.nodes_by_id[msg_id].metadata = {
        **(index.nodes_by_id[msg_id].metadata or {}),
        "turn_files": {"version": 2, "files": rows, "file_count": len(rows)},
    }

    first_ws = FakeWS()
    _run(tf.handle_review_scope(first_ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "scope": "turn", "category": "Tests", "query": "test_",
        "sort": "path", "cursor": 0, "limit": 20,
    }))
    first = first_ws.sent[0]["data"]
    assert first["status"] == "ready"
    assert first["file_count"] == 80
    assert first["added"] == sum(row["added"] for row in rows if row["rel"].startswith("tests/"))
    assert len(first["files"]) == 20
    assert all(row["rel"].startswith("tests/") for row in first["files"])
    assert first["next_cursor"] == 20

    next_ws = FakeWS()
    _run(tf.handle_review_scope(next_ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "scope": "turn", "category": "Tests", "query": "test_",
        "sort": "path", "cursor": first["next_cursor"], "limit": 20,
        "snapshot_id": first["snapshot_id"],
    }))
    assert len(next_ws.sent[0]["data"]["files"]) == 20

    stale_ws = FakeWS()
    _run(tf.handle_review_scope(stale_ws, {
        "session_id": session_id, "assistant_msg_id": msg_id,
        "scope": "turn", "category": "Docs", "query": "guide_",
        "sort": "path", "cursor": first["next_cursor"], "limit": 20,
        "snapshot_id": first["snapshot_id"],
    }))
    assert stale_ws.sent[0]["data"]["status"] == "stale"
    assert stale_ws.sent[0]["data"]["error"] == "STALE_SNAPSHOT"


def test_workspace_review_snapshot_rejects_worktree_content_change(store, tmp_path, monkeypatch):
    root = tmp_path / "snapshot-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    target = root / "tracked.py"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    target.write_text("after\n", encoding="utf-8")
    monkeypatch.setattr(tf, "_project_root", lambda _session_id: root)

    first = tf._workspace_scope("session", category="Code", query="tracked")
    assert first["status"] == "ready"
    assert first["files"][0]["base"]["kind"] == "blob"
    assert first["files"][0]["worktree"]["kind"] == "regular"
    diff = tf._workspace_file_diff("session", str(target), first["snapshot_id"])
    assert "after" in diff["diff"]
    subprocess.run(["git", "-C", str(root), "add", "tracked.py"], check=True)
    target.write_text("changed-after-snapshot\n", encoding="utf-8")

    ws = FakeWS()
    _run(tf.handle_review_scope(ws, {
        "session_id": "session", "scope": "workspace", "category": "Code",
        "query": "tracked", "snapshot_id": first["snapshot_id"],
        "cursor": 0, "limit": 100,
    }))
    data = ws.sent[0]["data"]
    assert data["status"] == "stale"
    assert data["error"] == "STALE_SNAPSHOT"


def test_diff_page_mounts_at_most_two_hundred_lines(store, tmp_path):
    session_id, msg_id = "s_line_page", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "many-lines.txt"
    target.write_text("", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("x\n" * 100_000, encoding="utf-8")
    journal.commit_after_edit(msg_id, str(target), operation="edit")

    first = tf._turn_file_diff(session_id, msg_id, str(target))
    second = tf._turn_file_diff(
        session_id, msg_id, str(target), first["next_cursor"],
    )

    assert first["line_count"] <= 200
    assert first["next_cursor"] is not None
    assert second["line_count"] <= 200
    assert second["diff"].startswith("@@")
    assert second["prev_cursor"] is None


def test_latest_file_turn_remains_undo_after_later_chat_only_reply(store, tmp_path):
    session_id = "s_latest_file"
    _seed(store, session_id, "a1")
    target = tmp_path / "latest.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit("a1", str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit("a1", str(target), operation="edit")
    store.append_message(session_id, {
        "id": "u2", "role": "user", "content": "explain", "predecessor": "a1",
    })
    store.append_message(session_id, {
        "id": "a2", "role": "assistant", "content": "text only", "predecessor": "u2",
    })

    result = tf._history_eligibility(session_id, "a1")

    assert result["status"] == "ready"
    assert result["action"] == "undo"
    assert result["latest_file_turn_id"] == "a1"


def test_history_action_hidden_when_current_digest_changed(store, tmp_path):
    session_id, msg_id = "s_history_conflict", "u1_reply"
    _seed(store, session_id, msg_id)
    target = tmp_path / "conflict.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    journal.backup_before_edit(msg_id, str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(msg_id, str(target), operation="edit")
    target.write_text("external\n", encoding="utf-8")

    result = tf._history_eligibility(session_id, msg_id)

    assert result["status"] == "blocked"
    assert result["action"] is None
    assert result["conflicts"] == [str(target)]


def test_card_file_expansion_is_capped_at_twenty(store, tmp_path):
    session_id, msg_id = "s_card_cap", "u1_reply"
    _seed(store, session_id, msg_id)
    journal = CheckpointStore(store._session_dir(session_id))
    for number in range(21):
        target = tmp_path / f"card-{number}.py"
        target.write_text("before\n", encoding="utf-8")
        journal.backup_before_edit(msg_id, str(target))
        target.write_text("after\n", encoding="utf-8")
        journal.commit_after_edit(msg_id, str(target), operation="edit")

    result = tf._list_files(session_id, msg_id)

    assert result["file_count"] == 21
    assert len(result["files"]) == 20
    assert result["truncated"] is True


def test_branch_net_stats_declines_large_repetitive_line_sets(store):
    from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir

    session_id = "s_stats_budget"
    _seed(store, session_id, "a1")
    session_dir = store._session_dir(session_id)
    before_raw = b"x\n" * 5_000
    after_raw = b"y\n" * 5_000
    states = []
    for turn_id, name, raw in (
        ("a1", "before", before_raw),
        ("a2", "after", after_raw),
    ):
        directory = turn_backup_dir(session_dir, turn_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(raw)
        states.append({
            "kind": "regular", "blob_ref": name, "size": len(raw),
            "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        })

    result = tf._net_stats(
        session_dir, "a1", states[0], "a2", states[1], [8 * 1024 * 1024],
    )

    assert result == (None, None, False, "timeout")


def test_turn_diff_rejects_symlinked_session_recovery_root(store, tmp_path):
    from openprogram.store.snapshot.checkpoint.paths import session_backup_root

    session_id = "s_symlink_recovery"
    _seed(store, session_id, "a1")
    session_dir = store._session_dir(session_id)
    external = tmp_path / "external-recovery"
    turn_dir = external / "a1"
    turn_dir.mkdir(parents=True)
    raw = b"outside\n"
    (turn_dir / "blob").write_bytes(raw)
    recovery_root = session_backup_root(session_dir)
    recovery_root.parent.mkdir(parents=True, exist_ok=True)
    recovery_root.symlink_to(external, target_is_directory=True)
    state = {
        "kind": "regular", "blob_ref": "blob", "size": len(raw),
        "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
    }

    with pytest.raises(OSError):
        tf._state_bytes(session_dir, "a1", state)
