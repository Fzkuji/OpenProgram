from pathlib import Path
from types import SimpleNamespace

from openprogram.store import _current_turn_id, _store
from openprogram.store.document_history import DocumentHistory
from openprogram.store.session.session_node_writer import SessionNodeWriter
from openprogram.store.snapshot.checkpoint.store import CheckpointStore
from openprogram.store.snapshot.checkpoint import manifest
from openprogram.store.snapshot.checkpoint.paths import turn_manifest_path
from openprogram.store.snapshot.checkpoint.helpers import (
    _project_locator, checkpoint_after_edit, checkpoint_before_edit,
)
from openprogram.store.project.identity import capture_directory_identity


def test_prepared_receipt_persists_project_locator(tmp_path):
    session = tmp_path / "session"
    project = tmp_path / "project"
    project.mkdir()
    target = project / "src" / "note.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    locator = {
        "project_id": "p1",
        "path": "src/note.txt",
        "recorded_root": str(project),
        "directory_identity": "dev:ino",
        "location_revision": 3,
    }

    CheckpointStore(session).backup_before_edit(
        "turn-1", str(target), project_locator=locator,
    )

    entry = manifest.load(turn_manifest_path(session, "turn-1"))["files"]
    assert next(iter(entry.values()))["project_locator"] == locator


def test_existing_prepared_receipt_does_not_rebind_locator(tmp_path):
    session = tmp_path / "session"
    project = tmp_path / "project"
    project.mkdir()
    target = project / "note.txt"
    target.write_text("before", encoding="utf-8")
    first = {
        "project_id": "p1", "path": "note.txt", "recorded_root": str(project),
        "directory_identity": "one", "location_revision": 1,
    }
    second = {**first, "project_id": "p2", "directory_identity": "two"}
    store = CheckpointStore(session)
    store.backup_before_edit("turn-1", str(target), project_locator=first)
    store.backup_before_edit("turn-1", str(target), project_locator=second)

    entry = manifest.load(turn_manifest_path(session, "turn-1"))["files"]
    assert next(iter(entry.values()))["project_locator"] == first


def test_helper_registers_real_document_history_row(tmp_path, monkeypatch):
    state = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    target = project_root / "note.txt"
    target.write_text("before", encoding="utf-8")
    from openprogram.store.session.session_store import SessionStore
    sessions = SessionStore(root_path=state / "sessions")
    sessions.create_session("s1", agent_id="a")
    sessions.update_session("s1", project_id="p1", project_path=str(project_root))
    project = SimpleNamespace(id="p1", path=str(project_root), is_default=False,
                              location_state="available", location_revision=2,
                              **capture_directory_identity(project_root))
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: state)
    monkeypatch.setattr("openprogram.store.project.project_for_session",
                        lambda sid: project if sid == "s1" else None)
    monkeypatch.setattr("openprogram.store.project.project_store.get_project",
                        lambda pid: project if pid == "p1" else None)
    monkeypatch.setattr("openprogram.store.default_store", lambda: sessions)
    shim = SessionNodeWriter(sessions, "s1")
    assert sessions.get_session("s1") is not None
    store_token = _store.set(shim)
    turn_token = _current_turn_id.set("turn-1")
    try:
        assert _project_locator(shim, str(target)) is not None
        assert checkpoint_before_edit(str(target))
        target.write_text("after", encoding="utf-8")
        assert checkpoint_after_edit(str(target))
    finally:
        _current_turn_id.reset(turn_token)
        _store.reset(store_token)
    rows = DocumentHistory(root=state / "project-file-history").list("p1", "note.txt")["entries"]
    sessions.close()
    assert rows and rows[0]["actor"] == "model"


def test_locator_rejects_replacement_registered_root(tmp_path, monkeypatch):
    original = tmp_path / "project"
    original.mkdir()
    project = SimpleNamespace(id="p1", path=str(original), is_default=False,
                              location_state="available", location_revision=1,
                              directory_identity=f"{original.stat().st_dev}:{original.stat().st_ino}")
    target = original / "note.txt"
    target.write_text("x", encoding="utf-8")
    shim = SimpleNamespace(session_id="s1", store=SimpleNamespace())
    monkeypatch.setattr("openprogram.store.project.project_for_session", lambda sid: project)
    moved = tmp_path / "moved"
    original.rename(moved)
    original.mkdir()
    target = original / "note.txt"
    target.write_text("replacement", encoding="utf-8")
    assert _project_locator(shim, str(target)) is None
