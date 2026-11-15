"""files.project.write: atomic project writes that never leave the project."""
import json
import os

import pytest

from openprogram.programs._host.ui import catalog


def test_write_replaces_a_project_file_atomically(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert catalog.write_project_file(project, "notes/today.md", "first") == {"path": "notes/today.md", "bytes": 5}
    assert catalog.write_project_file(project, "notes/today.md", b"second")["bytes"] == 6
    assert (project / "notes" / "today.md").read_text() == "second"
    assert [p.name for p in (project / "notes").iterdir()] == ["today.md"]


@pytest.mark.parametrize("relative", ["../outside.txt", "/etc/passwd", "a/../../outside.txt",
                                      ".git/config", "sub/../.git/HEAD", ".GIT/config", ".", ""])
def test_write_refuses_paths_outside_the_project_or_into_git(tmp_path, relative):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    with pytest.raises((ValueError, PermissionError)):
        catalog.write_project_file(project, relative, "x")
    assert not (tmp_path / "outside.txt").exists()
    assert list((project / ".git").iterdir()) == []


def test_write_refuses_a_symlink_that_leaves_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, project / "link")
    with pytest.raises(ValueError, match="escapes"):
        catalog.write_project_file(project, "link/file.txt", "x")
    os.symlink(project / ".git", project / "git-link")
    with pytest.raises(PermissionError):
        catalog.write_project_file(project, "git-link/config", "x")
    assert list(outside.iterdir()) == []


def _package(root, **extra):
    root.mkdir()
    (root / "index.html").write_text("<!doctype html>")
    (root / "backend.py").write_text("operations = {}\n")
    (root / "application.json").write_text(json.dumps({
        "id": "test.writer", "title": "Writer", "version": "1",
        "capabilities": ["files.project.write"], **extra}))
    return root


def test_the_write_capability_needs_a_backend_and_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="requires a Python backend"):
        catalog.manifest(_package(tmp_path / "static"))
    source = _package(tmp_path / "writer", backend={"kind": "python", "entry": "backend:operations"})
    definition = catalog.manifest(source)
    assert catalog.needs_trust(definition)
    with pytest.raises(ValueError, match="files.project.write") as refused:
        catalog.install(str(source))
    assert "trust=true" in str(refused.value)


def test_the_runner_offers_writes_only_with_the_capability():
    import inspect
    from openprogram.programs._host.ui import runner
    source = inspect.getsource(runner.main)
    assert 'self._require("files.project.write")' in source
    assert "write_project_file(project_path, relative, content)" in source
