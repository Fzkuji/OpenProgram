"""Session archiving: a reversible metadata flag that hides a session
from the default list without deleting anything or touching activity
time (see docs/reference/design/runtime/session/index-consistency.html).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from openprogram.store.session.session_store import SessionStore

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
    on_disk = json.loads((tmp_path / "sessions" / "gone" / "meta.json").read_text())
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
    """index.json is a cache — the flag's home is the session's meta.json."""
    store = _store(tmp_path)
    store.set_archived("gone", True)
    store._flush_index()
    store._index_path().unlink()

    reopened = SessionStore(tmp_path / "sessions")

    assert [r["id"] for r in reopened.list_sessions()] == ["keep"]


def test_set_archived_reports_unknown_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.set_archived("nope", True) is False


def test_reopen_does_not_enumerate_existing_history(tmp_path: Path, monkeypatch) -> None:
    """Startup cleanup must not list a remote-backed history directory."""
    root = tmp_path / "sessions"
    store = SessionStore(root)
    store.create_session(
        "existing-history",
        "main",
        created_at=time.time() - 7200.0,
        updated_at=time.time() - 7200.0,
    )
    store._flush_index()
    history = root / "existing-history" / "history"
    original_iterdir = Path.iterdir

    def fail_history_enumeration(path):
        if path == history:
            raise AssertionError("existing history must not be enumerated")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_history_enumeration)
    reopened = SessionStore(root)

    assert reopened.get_session("existing-history") is not None


def test_reopen_still_removes_old_session_without_history(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    store.create_session(
        "missing-history",
        "main",
        created_at=time.time() - 7200.0,
        updated_at=time.time() - 7200.0,
    )
    store._flush_index()
    shutil.rmtree(root / "missing-history" / "history")

    reopened = SessionStore(root)

    assert reopened.get_session("missing-history") is None
    assert not (root / "missing-history").exists()


def test_old_archives_are_never_deleted_on_startup(tmp_path):
    store = _store(tmp_path)
    store.set_archived("gone", True)
    store.update_session("gone", created_at=1, updated_at=1)
    store._flush_index()
    reopened = SessionStore(tmp_path / "sessions")
    assert reopened.get_session("gone") is not None
    assert reopened.get_session("gone")["archived"] is True
