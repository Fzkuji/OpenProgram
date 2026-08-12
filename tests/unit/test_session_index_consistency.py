from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from openprogram.context.nodes import Call
from openprogram.store.session.session_store import SessionStore
from openprogram.store.session.session_node_writer import SessionNodeWriter


def test_metadata_updates_preserve_activity_time_and_order(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("older", "main", title="old", updated_at=10.0)
    store.create_session("newer", "main", title="new", updated_at=20.0)

    store.update_session("older", title="renamed", pinned=True, unread=False)

    assert store.get_session("older")["updated_at"] == 10.0
    assert [row["id"] for row in store.list_sessions()] == ["newer", "older"]
    on_disk = json.loads((tmp_path / "sessions" / "older" / "meta.json").read_text())
    assert on_disk["updated_at"] == 10.0


def test_list_sessions_returns_row_snapshots(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="original", updated_at=10.0)

    rows = store.list_sessions()
    rows[0]["title"] = "caller mutation"

    assert store.list_sessions()[0]["title"] == "original"


def test_append_advances_meta_and_registry_with_one_timestamp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", updated_at=10.0)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.time.time",
        lambda: 30.0,
    )

    store.append_message("s1", {
        "id": "m1",
        "role": "user",
        "content": "hello",
        "predecessor": "",
    })

    assert store.get_session("s1")["updated_at"] == 30.0
    assert store.list_sessions()[0]["updated_at"] == 30.0
    on_disk = json.loads((tmp_path / "sessions" / "s1" / "meta.json").read_text())
    assert on_disk["updated_at"] == 30.0


def test_set_head_preserves_activity_time(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", updated_at=10.0)

    store.set_head("s1", None)

    assert store.get_session("s1")["updated_at"] == 10.0
    assert store.list_sessions()[0]["updated_at"] == 10.0
    on_disk = json.loads((tmp_path / "sessions" / "s1" / "meta.json").read_text())
    assert on_disk["updated_at"] == 10.0


@pytest.mark.parametrize(
    ("caller", "advance_head"),
    [("ROOT", True), ("", False)],
)
def test_node_append_syncs_activity_time_without_advancing_head(
    tmp_path: Path,
    caller: str,
    advance_head: bool,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", updated_at=10.0)

    SessionNodeWriter(store, "s1", advance_head=advance_head).append(Call(
        id="n1",
        role="tool" if caller else "user",
        output="content",
        caller=caller,
    ))

    activity_at = store.get_session("s1")["updated_at"]
    assert activity_at > 10.0
    assert store.list_sessions()[0]["updated_at"] == activity_at
    on_disk = json.loads((tmp_path / "sessions" / "s1" / "meta.json").read_text())
    assert on_disk["updated_at"] == activity_at


def test_update_during_deferred_write_remains_dirty_for_next_flush(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial", updated_at=10.0)
    store._update_index_entry("s1", title="old snapshot")
    with store._index_lock:
        store._index_dirty = True

    import openprogram.store.session.session_store as session_store_module

    real_write = session_store_module.atomic_write_text
    old_write_started = threading.Event()
    release_old_write = threading.Event()

    def delayed_write(path, text):
        if Path(path) == store._index_path() and '"old snapshot"' in text:
            old_write_started.set()
            assert release_old_write.wait(1)
        real_write(path, text)

    monkeypatch.setattr(session_store_module, "atomic_write_text", delayed_write)

    deferred = threading.Thread(target=store._do_deferred_flush)
    deferred.start()
    assert old_write_started.wait(1)

    store._update_index_entry("s1", title="new snapshot")
    release_old_write.set()
    deferred.join(1)

    assert not deferred.is_alive()
    assert store._index_dirty
    store._flush_index()
    persisted = json.loads(store._index_path().read_text())
    assert persisted["s1"]["title"] == "new snapshot"


def test_concurrent_direct_saves_cannot_restore_an_older_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", "main", title="initial", updated_at=10.0)

    import openprogram.store.session.session_store as session_store_module

    real_write = session_store_module.atomic_write_text
    old_write_started = threading.Event()
    release_old_write = threading.Event()

    def delayed_write(path, text):
        if Path(path) == store._index_path() and '"older write"' in text:
            old_write_started.set()
            assert release_old_write.wait(1)
        real_write(path, text)

    monkeypatch.setattr(session_store_module, "atomic_write_text", delayed_write)

    older = threading.Thread(
        target=store.update_session,
        args=("s1",),
        kwargs={"title": "older write"},
    )
    older.start()
    assert old_write_started.wait(1)

    newer = threading.Thread(
        target=store.update_session,
        args=("s1",),
        kwargs={"title": "newer write"},
    )
    newer.start()
    release_old_write.set()
    older.join(1)
    newer.join(1)

    assert not older.is_alive()
    assert not newer.is_alive()
    persisted = json.loads(store._index_path().read_text())
    assert persisted["s1"]["title"] == "newer write"
