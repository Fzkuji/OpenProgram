from __future__ import annotations

import subprocess
from pathlib import Path

from openprogram.programs import _programs, _registry
from openprogram.programs.workflow._project import catalog, repository, validation
import openprogram.paths as paths


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "state"
    root = tmp_path / "workflow"
    root.mkdir()
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: root)
    return root


def _plant(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir()
    (project / "pyproject.toml").write_text(
        catalog._project_pyproject(
            name,
            {"name": name, "summary": "Planted workflow", "tags": ["test"], "entrypoint": name},
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# planted\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "add", "--all"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c", "user.name=Attacker",
            "-c", "user.email=evil@example",
            "commit",
            "-m",
            "plant",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    return project


def _capture_imports(monkeypatch) -> list[str]:
    imported: list[str] = []
    real = _registry.importlib.import_module

    def fake(name, package=None):
        prefix = "openprogram.programs.workflow."
        if name.startswith(prefix) and "." not in name[len(prefix):]:
            imported.append(name[len(prefix):])
            return None
        return real(name, package)

    monkeypatch.setattr(_registry.importlib, "import_module", fake)
    return imported


def _candidate() -> dict:
    return validation._validate_project_candidate({
        "project_metadata": {
            "name": "trust_demo_workflow",
            "summary": "Import trust demo",
            "tags": ["test"],
        },
        "readme": "# Trust demo\n",
        "files": {
            "__init__.py": (
                "from .workflow import trust_demo_workflow\n\n"
                "__all__ = ['trust_demo_workflow']\n"
            ),
            "workflow.py": (
                "from openprogram.agentic_programming import agentic_function\n"
                "from .steps.work import work\n\n"
                "@agentic_function\n"
                "def trust_demo_workflow(task: str):\n"
                "    return work(task)\n"
            ),
            "steps/__init__.py": "",
            "steps/work.py": "def work(task: str):\n    return task\n",
            "tests/test_workflow.py": (
                "from workflows.trust_demo_workflow import trust_demo_workflow\n\n"
                "def test_entrypoint_is_callable():\n"
                "    assert callable(trust_demo_workflow)\n"
            ),
        },
    })


def test_unrecorded_workflow_directory_is_not_imported(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    _programs.mark_workflow_projects_migrated()
    _plant(root, "evil_workflow")
    imported = _capture_imports(monkeypatch)

    _registry._load_workflow_projects()

    assert imported == []
    assert _programs.owner_controlled_program_sources(str(root)) == []


def test_publish_records_workflow_and_import_loads_it(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    _programs.mark_workflow_projects_migrated()
    instance = tmp_path / "instance"
    instance.mkdir()
    repository._replace_snapshot(instance, _candidate())
    project_id, _revision = repository._publish_snapshot(
        instance,
        project_id="",
        action="create",
        metadata=_candidate()["project_metadata"],
    )
    imported = _capture_imports(monkeypatch)

    _registry._load_workflow_projects()

    assert project_id == "trust_demo_workflow"
    assert imported == ["trust_demo_workflow"]
    row = _programs.owner_controlled_program_sources(str(root))[0]
    assert row["kind"] == "workflow-publish"
    assert Path(row["path"]) == root / "trust_demo_workflow"


def test_startup_migrates_existing_valid_projects_once(tmp_path, monkeypatch):
    root = _isolate(tmp_path, monkeypatch)
    _plant(root, "legacy_workflow")
    imported = _capture_imports(monkeypatch)

    _registry._load_workflow_projects()

    assert imported == ["legacy_workflow"]
    row = _programs.owner_controlled_program_sources(str(root))[0]
    assert row["kind"] == "workflow-migration"
    assert _programs.workflow_projects_migrated()

    _plant(root, "late_plant_workflow")
    imported.clear()
    _registry._load_workflow_projects()

    assert imported == ["legacy_workflow"]
    assert [Path(item["path"]).name for item in _programs.owner_controlled_program_sources(str(root))] == [
        "legacy_workflow",
    ]
