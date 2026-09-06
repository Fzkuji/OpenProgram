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


def test_legacy_authorized_workflow_is_relocated_without_rescanning(tmp_path, monkeypatch):
    import json
    import openprogram

    state = tmp_path / "state"
    state.mkdir()
    package = tmp_path / "moved" / "openprogram"
    root = package / "programs" / "workflow"
    root.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(openprogram, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: root)
    _plant(root, "weekly_report")
    _plant(root, "unapproved_workflow")
    manifest = state / "program-sources.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "workflow_projects_migrated": True,
        "programs": [{
            "path": str(tmp_path / "missing" / "openprogram" / "programs" / "workflow" / "weekly_report"),
            "kind": "workflow-migration", "source": "workflow-migration",
        }],
    }))
    imported = _capture_imports(monkeypatch)
    _registry._load_workflow_projects()
    assert imported == ["weekly_report"]
    row = json.loads(manifest.read_text())["programs"][0]
    assert row["scope"] == "programs"
    assert row["path"] == "workflow/weekly_report"


def test_migration_preserves_existing_external_workflow(tmp_path, monkeypatch):
    import json
    import openprogram

    state = tmp_path / "state"
    state.mkdir()
    package = tmp_path / "active" / "openprogram"
    root = package / "programs" / "workflow"
    root.mkdir(parents=True)
    external_root = tmp_path / "external" / "openprogram" / "programs" / "workflow"
    external_root.mkdir(parents=True)
    external = _plant(external_root, "demo")
    _plant(root, "demo")
    monkeypatch.setattr(paths, "get_state_dir", lambda: state)
    monkeypatch.setattr(openprogram, "__file__", str(package / "__init__.py"))
    manifest = state / "program-sources.json"
    record = {"path": str(external), "kind": "workflow-publish", "source": "workflow:demo"}
    manifest.write_text(json.dumps({"version": 1, "programs": [record]}))
    _programs.migrate_program_source_paths()
    assert json.loads(manifest.read_text())["programs"] == [record]
    assert _programs.owner_controlled_program_sources(str(root)) == []
    assert _programs.owner_controlled_program_sources(str(external_root))[0]["path"] == str(external)


def test_bound_catalog_loads_real_packages_and_only_authorized_siblings(tmp_path, monkeypatch):
    import importlib
    import sys
    import pytest
    from openprogram.agentic_programming import function as function_module
    from openprogram.webui._functions import _discover_workflow_functions

    source = tmp_path / "source" / "openprogram" / "programs"
    root = source / "workflow"
    root.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(_registry._workflow_source_finder, "sources", {})
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    monkeypatch.setattr(function_module, "_registry", dict(function_module._registry))
    a = _plant(root, "portable_a")
    b = _plant(root, "portable_b")
    unapproved = _plant(root, "portable_unapproved")
    (b / "__init__.py").write_text("def value():\n    return 'dependency'\n")
    (unapproved / "__init__.py").write_text("raise AssertionError('must not import')\n")
    (a / "__init__.py").write_text("from .workflow import portable_a, late\n")
    (a / "workflow.py").write_text(
        "from openprogram.agentic_programming import agentic_function\n"
        "from openprogram.programs.workflow.portable_b import value\n"
        "@agentic_function\n"
        "def portable_a(task: str):\n    return value() + task\n"
        "def late():\n"
        "    from openprogram.programs.workflow.portable_b import value\n"
        "    return value()\n"
    )
    _programs.bind_program_catalog(source)
    for project in (a, b):
        _programs.record_program_source(project, source=f"workflow:{project.name}",
                                        kind="workflow-publish", base=str(root))
    _programs.mark_workflow_projects_migrated()
    prefix = "openprogram.programs.workflow.portable_"
    try:
        _registry._load_workflow_projects()
        module = importlib.import_module(prefix + "a")
        assert module.portable_a._fn(" task") == "dependency task"
        assert module.late() == "dependency"
        row = next(row for row in _discover_workflow_functions(set()) if row["name"] == "portable_a")
        assert row["filepath"] == str(a / "workflow.py")
        assert "task" in row["params"]
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(prefix + "unapproved")
    finally:
        parent = sys.modules["openprogram.programs.workflow"]
        for name in list(sys.modules):
            if name.startswith(prefix):
                sys.modules.pop(name, None)
        for name in ("portable_a", "portable_b", "portable_unapproved"):
            if hasattr(parent, name):
                delattr(parent, name)
