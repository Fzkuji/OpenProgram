"""Public contract: projects.json is never lost to a failed read or a race.

The worker, the CLI and agent subprocesses all read-modify-write the
project registry. A registry that cannot be read is kept for recovery
instead of being replaced by an empty one, and concurrent writers in
different processes never drop each other's changes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from openprogram.state.store.project import project_store as projects

REPO_ROOT = Path(__file__).resolve().parents[3]


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / ".openprogram"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    return state


def _registry(state: Path) -> Path:
    return state / "projects" / "projects.json"


def test_unparseable_registry_is_kept_instead_of_overwritten(tmp_path, monkeypatch):
    state = _isolate(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    projects.resolve_project(folder)
    original = _registry(state).read_bytes()
    _registry(state).write_bytes(original[: len(original) // 2])

    projects.get_default_project()

    kept = sorted(_registry(state).parent.glob("projects.json.unreadable-*"))
    assert [path.read_bytes() for path in kept] == [original[: len(original) // 2]]
    assert json.loads(_registry(state).read_text(encoding="utf-8"))


def test_registry_read_error_refuses_to_write(tmp_path, monkeypatch):
    state = _isolate(tmp_path, monkeypatch)
    folder = tmp_path / "paper"
    folder.mkdir()
    project = projects.resolve_project(folder)
    original = _registry(state).read_bytes()
    real_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        # Only this test's registry fails; other readers keep working.
        if self == _registry(state):
            raise PermissionError("synthetic read failure")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    with pytest.raises(projects.ProjectStoreError):
        projects.bind_session("s1", project.id)
    with pytest.raises(projects.ProjectStoreError):
        projects.get_default_project()
    assert _registry(state).read_bytes() == original


def test_writers_in_separate_processes_keep_every_change(tmp_path, monkeypatch):
    state = _isolate(tmp_path, monkeypatch)
    project = projects.get_default_project()
    writers, binds = 4, 25
    script = textwrap.dedent(
        """
        import sys
        from openprogram.state.store.project import project_store as projects
        tag, project_id, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
        for index in range(count):
            projects.bind_session(f"{tag}-{index}", project_id)
        """
    )
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(REPO_ROOT)}
    env.pop("OPENPROGRAM_PROFILE", None)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, f"w{writer}", project.id, str(binds)],
            cwd=REPO_ROOT, env=env,
        )
        for writer in range(writers)
    ]
    assert [child.wait(timeout=120) for child in children] == [0] * writers

    registry = json.loads(_registry(state).read_text(encoding="utf-8"))
    bound = set(registry[project.id]["session_ids"])
    assert bound == {f"w{w}-{i}" for w in range(writers) for i in range(binds)}
    assert not list(_registry(state).parent.glob("projects.json.unreadable-*"))
