"""A conversation folder that Windows holds is waited for, then left whole.

Windows refuses to rename a directory while any file inside it is open in
this or another process. Four operations rename a conversation folder:
moving a conversation to another project, binding a draft to a project,
quarantining a log-less shell at startup, and publishing a migrated copy.
Each either renames the folder or leaves it exactly where it was.

These tests name the platform through the filesystem seam's ``_sys`` and
stand a refusing ``os.rename`` in for the system call, so they run here;
they were not run on Windows.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.host._platform import filesystem
from openprogram.runtime.context.session_db import SessionDB
from openprogram.state.store.session.placement import SessionFolderBusy, move_session_folder


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "The process cannot access the file because it is being used by another process")
    error.winerror = 32
    return error


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(filesystem, "_sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(filesystem, "_SHARING_RETRY_SECONDS", 0.05)


@pytest.fixture
def held(monkeypatch):
    """``held[path] = n`` makes renaming ``path`` away refuse ``n`` times."""
    real = os.rename
    refusals: dict[str, int] = {}
    attempts: list[str] = []

    def rename(source, target, *args, **kwargs):
        key = os.fspath(source)
        attempts.append(key)
        if refusals.get(key, 0) > 0:
            refusals[key] -= 1
            raise _sharing_violation()
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)
    refusals_view = _Held(refusals, attempts)
    return refusals_view


class _Held:
    def __init__(self, refusals: dict[str, int], attempts: list[str]) -> None:
        self._refusals, self.attempts = refusals, attempts

    def hold(self, path: Path, times: int) -> None:
        self._refusals[str(path)] = times


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    db = SessionDB(state / "projects")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    return db


def _project(tmp_path: Path, name: str):
    from openprogram.state.store.project import project_store as P
    (tmp_path / name).mkdir(exist_ok=True)
    return P.resolve_project(tmp_path / name, name=name)


def _conversation(db, tmp_path: Path):
    """A conversation with one turn and a file-recovery tree in project a."""
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    db.create_session("s1", "main", project_id=a.id, title="paper")
    db.append_message("s1", {"id": "u1", "role": "user", "content": "hi", "timestamp": 0, "predecessor": None})
    folder = db._session_dir("s1")
    recovery = folder.parent / ".file-recovery" / "s1"
    (recovery / "t1").mkdir(parents=True)
    (recovery / "t1" / "x.txt").write_text("undo", encoding="utf-8")
    return a, b, folder, recovery


def _intact(db, a, folder: Path, recovery: Path) -> None:
    from openprogram.state.store.project import project_store as P
    assert (folder / "main.jsonl").is_file()
    assert (recovery / "t1" / "x.txt").read_text(encoding="utf-8") == "undo"
    assert db._session_dir("s1") == folder
    assert P.project_for_session("s1").id == a.id
    assert [m["id"] for m in db.get_messages("s1")] == ["u1"]
    assert db.get_session("s1")["title"] == "paper"


def test_a_move_waits_for_a_reader_holding_the_folder(env, tmp_path, windows, held):
    a, b, folder, recovery = _conversation(env, tmp_path)
    held.hold(folder, 2)
    held.hold(recovery, 2)

    assert env.move_session("s1", b.id)["moved"] is True

    after = env.root_path / b.id / "chats" / "s1"
    assert not folder.exists() and not recovery.exists()
    assert env._session_dir("s1") == after
    assert (after.parent / ".file-recovery" / "s1" / "t1" / "x.txt").read_text(encoding="utf-8") == "undo"
    assert [m["id"] for m in env.get_messages("s1")] == ["u1"]
    assert held.attempts.count(str(folder)) == 3


def test_a_folder_held_too_long_is_refused_and_nothing_moves(env, tmp_path, windows, held):
    a, b, folder, recovery = _conversation(env, tmp_path)
    held.hold(folder, 10_000)

    with pytest.raises(SessionFolderBusy) as refused:
        env.move_session("s1", b.id)

    assert str(folder) in str(refused.value) and "was not moved" in str(refused.value)
    assert not (env.root_path / b.id / "chats" / "s1").exists()
    _intact(env, a, folder, recovery)


def test_a_held_recovery_tree_puts_the_conversation_back(env, tmp_path, windows, held):
    a, b, folder, recovery = _conversation(env, tmp_path)
    held.hold(recovery, 10_000)

    with pytest.raises(SessionFolderBusy):
        env.move_session("s1", b.id)

    assert not (env.root_path / b.id / "chats" / "s1").exists()
    _intact(env, a, folder, recovery)


def test_a_conversation_that_cannot_go_back_stays_readable_and_says_where(env, tmp_path, windows, held):
    a, b, folder, recovery = _conversation(env, tmp_path)
    after = env.root_path / b.id / "chats" / "s1"
    held.hold(recovery, 10_000)
    held.hold(after, 10_000)

    with pytest.raises(SessionFolderBusy) as refused:
        env.move_session("s1", b.id)

    assert str(after) in str(refused.value) and str(recovery) in str(refused.value)
    assert env._session_dir("s1") == after
    assert [m["id"] for m in env.get_messages("s1")] == ["u1"]
    assert (recovery / "t1" / "x.txt").is_file()


def _stranded(env, tmp_path, held):
    """A move that could neither finish nor undo, with both holds released."""
    a, b, folder, recovery = _conversation(env, tmp_path)
    after = env.root_path / b.id / "chats" / "s1"
    held.hold(recovery, 10_000)
    held.hold(after, 10_000)
    with pytest.raises(SessionFolderBusy):
        env.move_session("s1", b.id)
    held.hold(recovery, 0)
    held.hold(after, 0)
    return a, b, folder, recovery, after


def test_moving_again_finishes_a_move_that_could_not_go_back(env, tmp_path, windows, held):
    from openprogram.state.store.project import project_store as P
    a, b, folder, recovery, after = _stranded(env, tmp_path, held)

    assert env.move_session("s1", b.id) == {"from_project_id": a.id, "project_id": b.id, "moved": True}

    assert (after.parent / ".file-recovery" / "s1" / "t1" / "x.txt").read_text(encoding="utf-8") == "undo"
    assert not recovery.exists() and not folder.exists()
    assert env._session_dir("s1") == after
    assert P.project_for_session("s1").id == b.id
    session = env.get_session("s1")
    assert session["project_id"] == b.id and session["project_history"] == [a.id]
    assert [m["id"] for m in env.get_messages("s1")] == ["u1"]


def test_moving_again_keeps_recovery_written_while_the_move_was_unfinished(env, tmp_path, windows, held):
    a, b, folder, recovery, after = _stranded(env, tmp_path, held)
    beside = after.parent / ".file-recovery" / "s1"
    (beside / "t2").mkdir(parents=True)
    (beside / "t2" / "y.txt").write_text("later", encoding="utf-8")

    assert env.move_session("s1", b.id)["moved"] is True

    assert (beside / "t1" / "x.txt").read_text(encoding="utf-8") == "undo"
    assert (beside / "t2" / "y.txt").read_text(encoding="utf-8") == "later"
    assert not recovery.exists()


def test_moving_again_while_the_recovery_tree_is_still_held_changes_nothing(env, tmp_path, windows, held):
    from openprogram.state.store.project import project_store as P
    a, b, folder, recovery, after = _stranded(env, tmp_path, held)
    held.hold(recovery, 10_000)

    with pytest.raises(SessionFolderBusy):
        env.move_session("s1", b.id)

    assert env._session_dir("s1") == after
    assert P.project_for_session("s1").id == a.id
    assert (recovery / "t1" / "x.txt").is_file()
    held.hold(recovery, 0)
    assert env.move_session("s1", b.id)["moved"] is True
    assert P.project_for_session("s1").id == b.id


def test_the_ws_move_reports_the_refusal(env, tmp_path, windows, held, monkeypatch):
    import asyncio
    import json

    from openprogram.interfaces.server.ws_actions import project as ws_project

    a, b, folder, recovery = _conversation(env, tmp_path)
    held.hold(folder, 10_000)
    sent: list[dict] = []

    class Socket:
        async def send_text(self, text: str) -> None:
            sent.append(json.loads(text))

    asyncio.run(ws_project.handle_move_session_to_project(Socket(), {"session_id": "s1", "project_id": b.id}))

    reply = next(frame["data"] for frame in sent if frame["type"] == "session_moved")
    assert reply["ok"] is False and reply["node_id"] is None
    assert "in use by another program and was not moved" in reply["error"]
    _intact(env, a, folder, recovery)


def test_a_draft_bound_to_a_project_keeps_its_folder_when_held(env, tmp_path, windows, held):
    from openprogram.state.store.project import project_store as P
    a = _project(tmp_path, "a")
    env.update_session("draft", model="m1")
    folder = env._session_dir("draft")
    assert folder.is_dir()
    held.hold(folder, 10_000)

    env.assign_project("draft", a.id)

    assert folder.is_dir() and not (env.root_path / a.id / "chats" / "draft").exists()
    assert env._session_dir("draft") == folder
    assert P.project_for_session("draft").id == a.id
    assert env.get_session("draft")["model"] == "m1"


def test_a_draft_bound_to_a_project_moves_after_a_brief_hold(env, tmp_path, windows, held):
    a = _project(tmp_path, "a")
    env.update_session("draft", model="m1")
    folder = env._session_dir("draft")
    held.hold(folder, 2)

    env.assign_project("draft", a.id)

    assert not folder.exists()
    assert env._session_dir("draft") == env.root_path / a.id / "chats" / "draft"
    assert env.get_session("draft")["model"] == "m1"


def test_a_held_draft_fails_the_create_and_keeps_its_settings(env, tmp_path, windows, held):
    a = _project(tmp_path, "a")
    env.update_session("draft", model="m1")
    folder = env._session_dir("draft")
    held.hold(folder, 10_000)

    with pytest.raises(SessionFolderBusy):
        env.create_session("draft", "main", project_id=a.id)

    assert env._session_dir("draft") == folder
    assert env.get_session("draft")["model"] == "m1"


def test_a_held_shell_is_not_quarantined_and_leaves_nothing_behind(tmp_path, windows, held):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    shell = tmp_path / "sessions" / "default" / "chats" / "s1"
    shell.mkdir(parents=True)
    held.hold(shell, 10_000)

    assert store._quarantine_shell("s1", shell, 100.0) is False

    assert shell.is_dir()
    quarantine = tmp_path / "sessions" / ".quarantine"
    assert not quarantine.exists() or list(quarantine.iterdir()) == []


def test_a_briefly_held_shell_is_quarantined(tmp_path, windows, held):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    shell = tmp_path / "sessions" / "default" / "chats" / "s1"
    shell.mkdir(parents=True)
    held.hold(shell, 2)

    assert store._quarantine_shell("s1", shell, 100.0) is True

    assert not shell.exists()
    assert (tmp_path / "sessions" / ".quarantine" / "s1-100" / "session").is_dir()


def test_posix_reports_a_refusal_at_once_as_it_is(tmp_path, held):
    source = tmp_path / "s1"
    source.mkdir()
    held.hold(source, 1)

    with pytest.raises(PermissionError) as refused:
        move_session_folder(source, tmp_path / "moved")

    assert not isinstance(refused.value, SessionFolderBusy)
    assert held.attempts == [str(source)] and source.is_dir()


def test_other_errors_are_not_retried_or_renamed(tmp_path, windows, held):
    with pytest.raises(FileNotFoundError):
        move_session_folder(tmp_path / "missing", tmp_path / "moved")
    assert len(held.attempts) == 1


def _legacy(tmp_path: Path, monkeypatch):
    """A conversation still in its working folder, with a recovery tree."""
    from openprogram.state.store.project import project_store as P
    from openprogram.state.store.session.session_log import SessionLog
    from openprogram.state.store.session.session_store import SessionStore

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    store = SessionStore(state / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    workdir = tmp_path / "paper"
    workdir.mkdir()
    project = P.resolve_project(workdir)
    source = workdir / ".openprogram" / "sessions" / "legacy1"
    source.mkdir(parents=True)
    log = SessionLog(source)
    log.write_meta({"id": "legacy1", "title": "kept", "project_id": project.id})
    log.write_history(1, "user", "u1", {"id": "u1", "role": "user", "content": "hello"})
    recovery = source.parent / ".file-recovery" / "legacy1" / "turn1"
    recovery.mkdir(parents=True)
    (recovery / "blob").write_text("before", encoding="utf-8")
    store._record_location("legacy1", source)
    P.bind_session("legacy1", project.id)
    store._index["legacy1"] = {"id": "legacy1", "title": "kept"}
    return store, project, source


def test_a_migrated_copy_is_published_after_a_scan_lets_go(tmp_path, monkeypatch, windows, held):
    from openprogram.state.store.session import migration
    from openprogram.state.store.session.placement import nested_session_dir

    store, project, source = _legacy(tmp_path, monkeypatch)
    staged = migration.staging_dir(store.root_path, "legacy1")
    held.hold(staged / "session", 2)
    held.hold(staged.parent / "legacy1.recovery", 2)

    assert migration.run_startup_migration(store)["legacy1"] == "done"

    destination = nested_session_dir(store.root_path, project.id, "legacy1")
    assert (destination / "main.jsonl").is_file() and not source.exists()
    assert (destination.parent / ".file-recovery" / "legacy1" / "turn1" / "blob").read_text(encoding="utf-8") == "before"


def test_a_migration_that_cannot_publish_keeps_the_source_and_resumes(tmp_path, monkeypatch, windows, held):
    from openprogram.state.store.session import migration
    from openprogram.state.store.session.placement import nested_session_dir

    store, project, source = _legacy(tmp_path, monkeypatch)
    staged = migration.staging_dir(store.root_path, "legacy1")
    held.hold(staged / "session", 10_000)

    assert migration.run_startup_migration(store)["legacy1"] == "failed"

    destination = nested_session_dir(store.root_path, project.id, "legacy1")
    row = migration.load_journal(store.root_path)["sessions"]["legacy1"]
    assert "in use by another program and was not moved" in row["error"]
    assert (source / "main.jsonl").is_file() and not destination.exists()
    assert store._session_dir("legacy1") == source

    held.hold(staged / "session", 0)
    assert migration.run_startup_migration(store)["legacy1"] == "done"
    assert (destination / "main.jsonl").is_file() and not source.exists()
