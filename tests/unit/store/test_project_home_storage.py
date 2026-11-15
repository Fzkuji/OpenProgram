"""Public contract: application-owned session storage and project identity.

Bound conversations live under the configured state root, grouped by a
stable project id. Working folders are locations, not storage. Copies
and same-path replacements do not inherit history.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from openprogram.state.store.project import project_store as projects
from openprogram.state.store.session.session_store import SessionStore


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    return state


def _store(tmp_path: Path, monkeypatch) -> SessionStore:
    state = _isolate(tmp_path, monkeypatch)
    store = SessionStore(state / "sessions")
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store", lambda: store)
    return store


def test_bound_session_is_not_written_into_workdir(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    workdir = tmp_path / "paper"
    workdir.mkdir()
    store.create_session("s1", "main", project_path=str(workdir), title="notes")
    proj = projects.project_for_session("s1")
    assert proj is not None and not proj.is_default
    nested = store.root_path / proj.id / "chats" / "s1"
    assert (nested / "main.jsonl").is_file()
    assert (nested / "main.jsonl").is_file()
    assert not (workdir / ".openprogram" / "sessions" / "s1").exists()
    assert store.get_session("s1")["title"] == "notes"


def test_new_project_id_is_opaque_uuid(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    proj = projects.resolve_project(folder)
    uuid.UUID(proj.id)
    assert not proj.id.startswith("proj_")


def test_metadata_edit_does_not_restamp_replaced_folder_identity(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    proj = projects.resolve_project(folder)
    original = proj.directory_identity
    assert original
    # Retain the original inode so Linux cannot recycle it for the replacement.
    folder.rename(tmp_path / "original-paper")
    folder.mkdir()
    projects.update_project(proj.id, {"name": "Paper notes"})
    saved = projects.get_project(proj.id)
    assert saved.name == "Paper notes"
    assert saved.directory_identity == original
    assert saved.directory_identity != f"{folder.stat().st_dev}:{folder.stat().st_ino}"


def test_copied_session_markers_do_not_auto_adopt_after_original_gone(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    original = tmp_path / "paper"
    original.mkdir()
    store.create_session("s1", "main", project_path=str(original))
    proj = projects.project_for_session("s1")
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    # Old session metadata alone is not a portable project identity.
    (copy / '.openprogram' / 'project.json').unlink()
    marker = copy / ".openprogram" / "sessions" / "s1"
    marker.mkdir(parents=True, exist_ok=True)
    (marker / "main.jsonl").touch()
    shutil.rmtree(original)
    other = projects.resolve_project(copy)
    assert other.id != proj.id
    assert projects.get_project(proj.id).path == str(original.resolve())


def test_apply_default_workdir_does_not_fallback_when_bound_folder_missing(
        tmp_path, monkeypatch):
    from openprogram.runtime.conversation import workdir as _workdir

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    folder = tmp_path / "paper"
    folder.mkdir()
    store.create_session("s1", "main", title="notes")
    proj = projects.resolve_project(folder)
    projects.unbind_session("s1")
    projects.bind_session("s1", proj.id)
    store.update_session("s1", project_id=proj.id)
    store.append_message("s1", {
        "id": "u1", "role": "user", "content": "hi", "predecessor": None,
    })
    shutil.rmtree(folder)

    class Runtime:
        def __init__(self):
            self.workdir = None

        def set_workdir(self, path: str) -> None:
            self.workdir = path

    rt = Runtime()
    assert (store.root_path / "default" / "chats" / "s1" / "main.jsonl").is_file() or (
        store.root_path / proj.id / "chats" / "s1" / "main.jsonl").is_file()
    assert _workdir.project_workdir_for("s1") is None
    assert _workdir.apply_default_workdir(rt, "s1") is None
    assert rt.workdir is None


def test_default_discovery_does_not_scan_home(tmp_path, monkeypatch):
    from openprogram.state.store.project import discovery as discovery_mod

    _isolate(tmp_path, monkeypatch)
    fake_home = tmp_path / "home"
    planted = fake_home / "Documents" / "paper"
    planted.mkdir(parents=True)
    old = tmp_path / "old"
    old.mkdir()
    proj = projects.resolve_project(old)
    shutil.rmtree(old)
    relocated = discovery_mod.discover_moved_projects(roots=[fake_home])
    assert relocated == []
    assert Path(projects.get_project(proj.id).path) == old
