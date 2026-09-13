"""Recoverable legacy migration: journal, external recovery, restart, delete."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.store.project import project_store as projects
from openprogram.store.session.migration import (
    collect_legacy_candidates,
    load_journal,
    migrate_session,
    run_startup_migration,
    staging_dir,
)
from openprogram.store.session import migration
from openprogram.store.session.placement import (
    delete_intent_path,
    is_deleted,
    nested_session_dir,
    record_delete_intent,
)
from openprogram.store.session.session_store import SessionStore
from openprogram.store.session.session_store import SessionPlacementError


def _isolate(tmp_path: Path, monkeypatch) -> SessionStore:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
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


def test_session_placement_lookup_failure_does_not_fallback_to_default(
    tmp_path, monkeypatch,
):
    # This assertion covers the managed/default placement contract. An
    # explicitly rooted standalone store intentionally has no project
    # registry to consult.
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    store = SessionStore()

    def fail_lookup(_session_id):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(projects, "project_for_session", fail_lookup)
    with pytest.raises(SessionPlacementError):
        store._session_dir("unresolved")


def test_fresh_explicit_store_prefers_nested_session_over_stale_location(
    tmp_path, monkeypatch,
):
    store = _isolate(tmp_path, monkeypatch)
    workdir = tmp_path / "project"
    workdir.mkdir()
    store.create_session("nested1", "main", project_path=str(workdir))
    project = projects.project_for_session("nested1")
    nested = nested_session_dir(store.root_path, project.id, "nested1")
    stale = tmp_path / "gone" / "nested1"
    store._record_location("nested1", stale)

    fresh = SessionStore(store.root_path)
    assert fresh._session_dir("nested1") == nested


def test_repeating_legacy_candidate_preserves_completed_migration(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    _project, sid, source, _recovery = _legacy_session(tmp_path, store)
    entry = collect_legacy_candidates(store)[0]

    assert run_startup_migration(store)[sid] == "done"
    stale = dict(entry)
    stale["source_unavailable"] = True
    assert migrate_session(store, stale) == "done"
    assert not source.exists()
    assert load_journal(store.root_path)["sessions"][sid]["stage"] == "done"


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


def test_migration_keeps_source_when_staged_file_fsync_fails(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    _project, sid, source, _recovery = _legacy_session(tmp_path, store)
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync
    staged_fds = set()

    def track_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(staging_dir(store.root_path, sid)) in str(path):
            staged_fds.add(fd)
        return fd

    def fail_staged_fsync(fd):
        if fd in staged_fds:
            raise OSError("injected staged fsync failure")
        return real_fsync(fd)

    def forget_closed_fd(fd):
        staged_fds.discard(fd)
        return real_close(fd)

    monkeypatch.setattr(migration.os, "open", track_open)
    monkeypatch.setattr(migration.os, "close", forget_closed_fd)
    monkeypatch.setattr(migration.os, "fsync", fail_staged_fsync)

    assert migrate_session(store, collect_legacy_candidates(store)[0]) == "failed"
    assert source.exists()
    assert not nested_session_dir(store.root_path, _project.id, sid).exists()
    assert load_journal(store.root_path)["sessions"][sid]["stage"] == "failed"


def test_migration_flushes_nested_files_without_following_symlinks(
    tmp_path, monkeypatch,
):
    store = _isolate(tmp_path, monkeypatch)
    project, sid, source, _recovery = _legacy_session(tmp_path, store)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not open", encoding="utf-8")
    link = source / "history" / "outside-link"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync
    staged_fds = {}
    flushed_paths = []

    def track_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(staging_dir(store.root_path, sid)) in str(path):
            staged_fds[fd] = str(path)
        return fd

    def record_fsync(fd):
        if fd in staged_fds:
            flushed_paths.append(staged_fds[fd])
        return real_fsync(fd)

    def forget_closed_fd(fd):
        staged_fds.pop(fd, None)
        return real_close(fd)

    monkeypatch.setattr(migration.os, "open", track_open)
    monkeypatch.setattr(migration.os, "close", forget_closed_fd)
    monkeypatch.setattr(migration.os, "fsync", record_fsync)

    assert migrate_session(store, collect_legacy_candidates(store)[0]) == "done"
    staged = str(staging_dir(store.root_path, sid))
    assert any(path.endswith("/session/history/0001-u-u1.json") for path in flushed_paths)
    assert any(path.endswith("/session/history") for path in flushed_paths)
    assert any(path.endswith("/session/file_backups/old") for path in flushed_paths)
    assert any(path.endswith("/session/file_backups") for path in flushed_paths)
    assert any(path.endswith(f"/{sid}.recovery/turn1") for path in flushed_paths)
    assert not any(path.endswith("/session/history/outside-link") for path in flushed_paths)
    assert staged not in flushed_paths
    assert outside.read_text(encoding="utf-8") == "do not open"


def test_migration_keeps_source_when_nested_directory_fsync_fails(tmp_path, monkeypatch):
    store = _isolate(tmp_path, monkeypatch)
    project, sid, source, _recovery = _legacy_session(tmp_path, store)
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync
    staged_fds = {}

    def track_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(staging_dir(store.root_path, sid)) in str(path):
            staged_fds[fd] = str(path)
        return fd

    def fail_nested_directory_fsync(fd):
        if staged_fds.get(fd, "").endswith("/session/history"):
            raise OSError("injected nested directory fsync failure")
        return real_fsync(fd)

    def forget_closed_fd(fd):
        staged_fds.pop(fd, None)
        return real_close(fd)

    monkeypatch.setattr(migration.os, "open", track_open)
    monkeypatch.setattr(migration.os, "close", forget_closed_fd)
    monkeypatch.setattr(migration.os, "fsync", fail_nested_directory_fsync)

    assert migrate_session(store, collect_legacy_candidates(store)[0]) == "failed"
    assert source.exists()
    assert not nested_session_dir(store.root_path, project.id, sid).exists()


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


def test_migration_flush_uses_writable_handles_on_windows(tmp_path, monkeypatch):
    from types import SimpleNamespace

    store = _isolate(tmp_path, monkeypatch)
    project, sid, source, _recovery = _legacy_session(tmp_path, store)
    descriptors = {}

    def open_file(path, flags, *args, **kwargs):
        fd = os.open(path, flags, *args, **kwargs)
        descriptors[fd] = flags
        return fd

    def flush_file(fd):
        if not descriptors[fd] & os.O_RDWR:
            raise OSError("Windows file flush requires write access")
        return os.fsync(fd)

    def close_file(fd):
        descriptors.pop(fd, None)
        return os.close(fd)

    windows_os = SimpleNamespace(**vars(os))
    windows_os.name = "nt"
    windows_os.open = open_file
    windows_os.fsync = flush_file
    windows_os.close = close_file
    monkeypatch.setattr(migration, "os", windows_os)
    result = migrate_session(store, {
        "session_id": sid, "project_id": project.id, "source": str(source),
    })
    assert result == "done", load_journal(store.root_path)
    destination = nested_session_dir(store.root_path, project.id, sid)
    assert json.loads((destination / "meta.json").read_text())["title"] == "kept"
    assert not source.exists()
    assert not descriptors
