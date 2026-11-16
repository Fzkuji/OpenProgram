"""The home project is named after the home folder, not a fixed label."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.state.store.project import project_store as projects


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / ".openprogram"
    state.mkdir()
    folder = tmp_path / "ada"
    folder.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    return folder


def test_a_new_home_project_takes_the_home_folders_name(home):
    project = projects.get_default_project()
    assert project.name == "ada"
    assert project.path == str(home)


def test_a_name_the_user_chose_is_kept(home):
    projects.get_default_project()
    projects.update_project("default", {"name": "System"})
    assert projects.get_default_project().name == "System"
