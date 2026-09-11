from pathlib import Path
import shutil

from openprogram.store.project import project_store as projects
from openprogram.store.project.discovery import discover_moved_projects


def test_discovers_renamed_folder_preserving_project(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr('openprogram.store.session.session_store.default_store', lambda: type('Store', (), {'relocate_project_sessions': lambda *args: 0})())
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    new = tmp_path / 'renamed'; old.rename(new)
    assert discover_moved_projects([tmp_path]) == [project.id]
    assert projects.get_project(project.id).path == str(new)
    assert projects.resolve_project(new).id == project.id


def test_ambiguous_session_copies_are_not_claimed(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    projects.bind_session('s1', project.id)
    (old / '.openprogram/sessions/s1').mkdir(parents=True)
    shutil.copytree(old, tmp_path / 'copy')
    old.rename(tmp_path / 'moved')
    assert discover_moved_projects([tmp_path]) == []
    assert projects.get_project(project.id).path == str(old)


def test_incomplete_search_and_existing_original_do_not_relocate(tmp_path, monkeypatch):
    monkeypatch.setattr('openprogram.paths.get_state_dir', lambda: str(tmp_path / 'state'))
    old = tmp_path / 'old'; old.mkdir()
    project = projects.resolve_project(old)
    assert discover_moved_projects([tmp_path]) == []
    old.rename(tmp_path / 'new')
    assert discover_moved_projects([tmp_path], max_directories=1) == []
    assert projects.get_project(project.id).path == str(old)
