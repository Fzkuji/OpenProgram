"""Session archiving: a reversible metadata flag that hides a session
from the default list without deleting anything or touching activity
time (see docs/reference/design/runtime/session/index-consistency.html).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from openprogram.store.session.session_store import SessionStore

# Wall-clock-relative: _startup_cleanup deletes archived sessions idle
# for 90 days and empty shells older than an hour, so epoch-1970
# fixtures would be swept away on the next open.
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
