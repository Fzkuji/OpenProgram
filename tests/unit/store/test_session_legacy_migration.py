"""Recoverable legacy migration: journal, external recovery, restart, delete."""
from __future__ import annotations

import json
from pathlib import Path

from openprogram.store.project import project_store as projects
from openprogram.store.session.migration import (
    collect_legacy_candidates,
    load_journal,
    migrate_session,
    run_startup_migration,
)
from openprogram.store.session.placement import (
    delete_intent_path,
    is_deleted,
    nested_session_dir,
    record_delete_intent,
)
from openprogram.store.session.session_store import SessionStore


def _isolate(tmp_path: Path, monkeypatch) -> SessionStore:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: str(state))
    store = SessionStore(state / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: store)
    return store


def _legacy_session(tmp_path: Path, store: SessionStore, name: str = "paper"):
    workdir = tmp_path / name
    workdir.mkdir()
    proj = projects.resolve_project(workdir)
    sid = "legacy1"
    source = workdir / ".openprogram" / "sessions" / sid
    source.mkdir(parents=True)
    (source / "history").mkdir()
    (source / "meta.json").write_text(
        json.dumps({"id": sid, "title": "kept", "project_id": proj.id}),
        encoding="utf-8")
    (source / "history" / "0001-u-u1.json").write_text(
        json.dumps({"id": "u1", "role": "user", "content": "hello"}),
        encoding="utf-8")
    recovery = source.parent / ".file-recovery" / sid
    recovery.mkdir(parents=True)
    (recovery / "turn1").mkdir()
    (recovery / "turn1" / "blob").write_text("before", encoding="utf-8")
    (source / "file_backups" / "old").mkdir(parents=True)
    (source / "file_backups" / "old" / "x").write_text("internal", encoding="utf-8")
    store._record_location(sid, source)
    projects.bind_session(sid, proj.id)
    store._index[sid] = {"id": sid, "title": "kept"}
    return proj, sid, source, recovery


def test_migration_publishes_session_and_external_recovery(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    proj, sid, source, recovery = _legacy_session(tmp_path, store)
    result = run_startup_migration(store)
    assert result[sid] == "done"
    dest = nested_session_dir(store.root_path, proj.id, sid)
    assert (dest / "meta.json").is_file()
    assert (dest / "history" / "0001-u-u1.json").read_text(encoding="utf-8")
    external = dest.parent / ".file-recovery" / sid
    assert (external / "turn1" / "blob").read_text(encoding="utf-8") == "before"
    assert not source.exists()
    assert not recovery.exists()
    assert json.loads((dest / "meta.json").read_text())["title"] == "kept"


def test_unavailable_source_stays_pending_not_empty(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    proj, sid, source, _recovery = _legacy_session(tmp_path, store)
    import shutil
    shutil.rmtree(source.parent)
    result = migrate_session(store, {
        "session_id": sid, "project_id": proj.id,
        "source": str(source), "source_unavailable": True,
    })
    assert result == "pending"
    dest = nested_session_dir(store.root_path, proj.id, sid)
    assert not dest.exists()
    row = load_journal(store.root_path)["sessions"][sid]
    assert row["stage"] == "pending"
    assert sid in (projects.get_project(proj.id).session_ids or [])


def test_delete_intent_prevents_resurrection(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    proj, sid, source, _recovery = _legacy_session(tmp_path, store)
    record_delete_intent(store.root_path, sid, {"session_id": sid})
    result = migrate_session(store, {
        "session_id": sid, "project_id": proj.id, "source": str(source),
    })
    assert result == "deleted"
    assert is_deleted(store.root_path, sid)
    assert not nested_session_dir(store.root_path, proj.id, sid).exists()
    assert delete_intent_path(store.root_path, sid).is_file()


def test_interrupted_copy_resumes_without_clobber(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    proj, sid, source, _recovery = _legacy_session(tmp_path, store)
    dest = nested_session_dir(store.root_path, proj.id, sid)
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(json.dumps({"id": sid, "title": "other"}), encoding="utf-8")
    (dest / "history").mkdir()
    result = migrate_session(store, {
        "session_id": sid, "project_id": proj.id, "source": str(source),
    })
    assert result == "failed"
    assert json.loads((dest / "meta.json").read_text())["title"] == "other"
    assert source.exists()


def test_collect_skips_home_owned_sessions(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    workdir = tmp_path / "paper"
    workdir.mkdir()
    store.create_session("s1", "main", project_path=str(workdir))
    assert collect_legacy_candidates(store) == []


def test_unrelated_existing_destination_is_preserved_on_conflict(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    proj, sid, source, _recovery = _legacy_session(tmp_path, store)
    dest = nested_session_dir(store.root_path, proj.id, sid)
    dest.mkdir(parents=True)
    marker = dest / "unrelated.txt"
    marker.write_text("keep", encoding="utf-8")
    assert migrate_session(store, {
        "session_id": sid, "project_id": proj.id, "source": str(source),
    }) == "failed"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert source.exists()
