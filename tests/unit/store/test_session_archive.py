"""Session archiving: a reversible metadata flag that hides a session
from the default list without deleting anything or touching activity
time (see docs/reference/design/runtime/session/storage-consistency.html).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from openprogram.state.store.session.session_log import SessionLog
from openprogram.state.store.session.session_store import SessionStore

# Wall-clock-relative for unarchived empty-shell cleanup. Explicit
# archives are retained indefinitely.
OLDER = time.time() - 60.0
NEWER = time.time()


def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("keep", "main", title="Keep", updated_at=OLDER)
    store.create_session("gone", "main", title="Gone", updated_at=NEWER)
    return store


def test_archived_sessions_drop_out_of_the_default_list(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.set_archived("gone", True) is True

    assert [r["id"] for r in store.list_sessions()] == ["keep"]
    assert store.count_sessions() == 1


def test_include_archived_returns_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("gone", True)

    rows = store.list_sessions(include_archived=True)

    assert [r["id"] for r in rows] == ["gone", "keep"]
    assert store.count_sessions(include_archived=True) == 2


def test_archived_filter_selects_only_archived(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("gone", True)

    assert [r["id"] for r in store.list_sessions(archived=True)] == ["gone"]


def test_archiving_preserves_activity_time_and_order(tmp_path: Path) -> None:
    """The index-consistency contract: only appending a message is
    activity, so archiving must not reorder the sidebar."""
    store = _store(tmp_path)

    store.set_archived("gone", True)
    store.set_archived("gone", False)

    assert store.get_session("gone")["updated_at"] == NEWER
    assert [r["id"] for r in store.list_sessions()] == ["gone", "keep"]
    on_disk = SessionLog(tmp_path / "sessions" / "default" / "chats" / "gone").read_meta()
    assert on_disk["updated_at"] == NEWER


def test_unarchive_restores_the_session_and_its_messages(tmp_path: Path) -> None:
    """Archiving is a flag, never a delete — history survives it."""
    store = _store(tmp_path)
    store.append_message("gone", {
        "id": "m1", "role": "user", "content": "hello", "predecessor": "",
    })
    before = store.get_messages("gone")

    store.set_archived("gone", True)
    assert store.get_messages("gone") == before   # readable while archived
    store.set_archived("gone", False)

    assert [r["id"] for r in store.list_sessions()] == ["gone", "keep"]
    assert store.get_messages("gone") == before


def test_archive_flag_survives_a_reload_from_disk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_archived("gone", True)
    store._flush_index()

    reopened = SessionStore(tmp_path / "sessions")

    assert [r["id"] for r in reopened.list_sessions()] == ["keep"]
    assert [r["id"] for r in reopened.list_sessions(archived=True)] == ["gone"]


def test_archive_flag_survives_an_index_rebuild(tmp_path: Path) -> None:
    """index.json is a cache — the flag's home is the session's log."""
    store = _store(tmp_path)
    store.set_archived("gone", True)
    store._flush_index()
    store._index_path().unlink()

    reopened = SessionStore(tmp_path / "sessions")

    assert [r["id"] for r in reopened.list_sessions()] == ["keep"]


def test_set_archived_reports_unknown_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.set_archived("nope", True) is False


def test_session_removal_handles_windows_readonly_git_objects(tmp_path, monkeypatch):
    """Deleting a session removes read-only Windows Git objects."""
    import errno
    import os
    import stat
    from types import SimpleNamespace

    original_unlink = os.unlink
    original_lstat = os.lstat
    name = 'readonly-git-object'

    def windows_unlink(path, *, dir_fd=None):
        if os.path.basename(path) == name:
            info = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            if not info.st_mode & stat.S_IWRITE:
                raise PermissionError(errno.EACCES, 'read-only Git object', path)
        return original_unlink(path, dir_fd=dir_fd)

    def windows_lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if os.path.basename(path) != name:
            return info
        return SimpleNamespace(
            st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino,
            st_file_attributes=1 if not info.st_mode & stat.S_IWRITE else 0,
        )

    monkeypatch.setattr(os, 'unlink', windows_unlink)
    monkeypatch.setattr(os, 'lstat', windows_lstat)
    root = tmp_path / 'sessions'
    store = SessionStore(root)
    store.create_session('old', 'main', created_at=time.time() - 7200.0,
                         updated_at=time.time() - 7200.0)
    store.close()
    directory = root / 'default' / 'chats' / 'old'
    git_object = directory / 'agents' / name
    git_object.parent.mkdir(exist_ok=True)
    git_object.write_bytes(b'object')
    git_object.chmod(0o444)
    store.delete_session('old')
    assert not directory.exists()


def test_old_archives_are_never_deleted_on_startup(tmp_path):
    store = _store(tmp_path)
    store.set_archived("gone", True)
    store.update_session("gone", created_at=1, updated_at=1)
    store._flush_index()
    reopened = SessionStore(tmp_path / "sessions")
    assert reopened.get_session("gone") is not None
    assert reopened.get_session("gone")["archived"] is True
