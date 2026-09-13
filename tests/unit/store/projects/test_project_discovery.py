from pathlib import Path
import shutil

from openprogram.store.project import project_store as projects
from openprogram.store.project.discovery import discover_moved_projects


def test_discovers_renamed_folder_preserving_project(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args, **kwargs: 0})())
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    new = tmp_path / 'renamed'; old.rename(new)
    assert discover_moved_projects() == [project.id]
    assert projects.get_project(project.id).path == str(new)
    assert projects.resolve_project(new).id == project.id
    old.mkdir()
    replacement = projects.resolve_project(old)
    assert replacement.id != project.id
    assert replacement.path == str(old)
    assert projects.resolve_project(old).id == replacement.id


def test_ambiguous_session_copies_are_not_claimed(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    projects.bind_session('s1', project.id)
    (old / '.openprogram/sessions/s1').mkdir(parents=True)
    shutil.copytree(old, tmp_path / 'copy')
    old.rename(tmp_path / 'moved')
    relocated = discover_moved_projects()
    assert relocated == [project.id]
    assert Path(projects.get_project(project.id).path) == (tmp_path / 'moved')
    copy = projects.resolve_project(tmp_path / 'copy')
    assert copy.id != project.id


def test_incomplete_search_and_existing_original_do_not_relocate(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    assert discover_moved_projects() == []
    old.rename(tmp_path / 'new')
    assert discover_moved_projects(max_directories=1) == [project.id]


def test_missing_folder_keeps_session_evidence_during_list_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    projects.bind_session('s1', project.id)
    old.rename(tmp_path / 'new')
    assert projects.prune_sessions(set()) == 0
    assert projects.get_project(project.id).session_ids == ['s1']
