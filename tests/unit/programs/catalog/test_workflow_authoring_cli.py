from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from openprogram.programs.workflow._project import catalog, repository, validation


def _write_project(
    root: Path,
    *,
    invalid_import: bool = False,
    display_name: str = "demo_workflow",
) -> Path:
    project = root / "demo_workflow"
    (project / "steps").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "pyproject.toml").write_text(
        catalog._project_pyproject(
            "demo_workflow",
            {
                "name": display_name,
                "summary": "Validate a demo workflow",
                "tags": ["demo"],
                "entrypoint": "demo_workflow",
            },
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Demo workflow\n", encoding="utf-8")
    (project / "__init__.py").write_text(
        f"from .workflow import {display_name}\n",
        encoding="utf-8",
    )
    workflow_import = (
        "import os\n"
        if invalid_import
        else "from openprogram.agentic_programming import agentic_function\n"
    )
    (project / "workflow.py").write_text(
        workflow_import
        + "from .steps.work import work\n\n"
        + "@agentic_function\n"
        + f"def {display_name}(task: str):\n"
        + "    return work(task)\n",
        encoding="utf-8",
    )
    (project / "steps" / "work.py").write_text(
        "def work(task: str):\n    return task\n",
        encoding="utf-8",
    )
    # Static validation must parse this file, never execute it.
    (project / "tests" / "test_workflow.py").write_text(
        "def test_not_run_by_static_validation():\n"
        "    raise RuntimeError('static validation executed candidate code')\n",
        encoding="utf-8",
    )
    return project


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_directory_validation_uses_the_package_contract_without_writing(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    before = _tree_bytes(project)

    report = validation.validate_workflow_directory(project)

    assert report == {
        "ok": True,
        "workflow_id": "demo_workflow",
        "metadata": {
            "name": "demo_workflow",
            "summary": "Validate a demo workflow",
            "tags": ["demo"],
            "entrypoint": "demo_workflow",
        },
        "files": [
            "__init__.py",
            "steps/work.py",
            "tests/test_workflow.py",
            "workflow.py",
        ],
        "executed_tests": False,
    }
    assert _tree_bytes(project) == before
    assert not (project / ".git").exists()


def test_directory_validation_ignores_python_bytecode_cache(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    cache = project / "__pycache__"
    nested_cache = project / "steps" / "__pycache__"
    cache.mkdir()
    nested_cache.mkdir()
    (cache / "workflow.cpython-312.pyc").write_bytes(b"cached bytecode")
    (nested_cache / "work.cpython-312.pyc").write_bytes(b"cached bytecode")
    before = _tree_bytes(project)

    report = validation.validate_workflow_directory(project)

    assert report["ok"] is True
    assert report["workflow_id"] == "demo_workflow"
    assert not any("__pycache__" in path for path in report["files"])
    assert _tree_bytes(project) == before


def test_directory_validation_rejects_source_hidden_in_bytecode_cache(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    cache = project / "__pycache__"
    cache.mkdir()
    (cache / "hidden.py").write_text("def hidden():\n    return True\n", encoding="utf-8")

    with pytest.raises(validation.InvalidWorkflow, match="only bytecode"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_symlinked_bytecode_cache(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    external = tmp_path / "external-cache"
    external.mkdir()
    (external / "hidden.py").write_text("def hidden():\n    return True\n", encoding="utf-8")
    try:
        (project / "__pycache__").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(validation.InvalidWorkflow, match="must not be symlinks"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_nested_bytecode_cache_directory(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "__pycache__" / "nested").mkdir(parents=True)

    with pytest.raises(validation.InvalidWorkflow, match="only bytecode"):
        validation.validate_workflow_directory(project)


def test_workflows_validate_cli_supports_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from openprogram import cli

    project = _write_project(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["openprogram", "workflows", "validate", str(project), "--json"],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "demo_workflow"
    assert payload["executed_tests"] is False


def test_workflows_validate_cli_reports_invalid_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from openprogram import cli

    project = _write_project(tmp_path, invalid_import=True)
    monkeypatch.setattr(
        sys,
        "argv",
        ["openprogram", "workflows", "validate", str(project), "--json"],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_type"] == "InvalidWorkflow"
    assert "may not use import statements" in payload["error"]


def test_workflows_validate_cli_rejects_mismatched_project_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from openprogram import cli

    project = _write_project(tmp_path, display_name="other_workflow")
    monkeypatch.setattr(
        sys,
        "argv",
        ["openprogram", "workflows", "validate", str(project), "--json"],
    )

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "display-name must match" in payload["error"]


def test_directory_validation_rejects_a_second_public_entry(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    with (project / "workflow.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n@agentic_function\n"
            "def extra_workflow(task: str):\n"
            "    return task\n"
        )

    with pytest.raises(validation.InvalidWorkflow, match="exactly one public"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_executable_dunder_all(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    (project / "__init__.py").write_text(
        "from .workflow import demo_workflow\n"
        "\n"
        "def explode():\n"
        "    raise RuntimeError('executed')\n"
        "\n"
        "__all__ = explode()\n",
        encoding="utf-8",
    )

    with pytest.raises(validation.InvalidWorkflow, match="string literal list"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_renamed_entrypoint_export(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "__init__.py").write_text(
        "from .workflow import demo_workflow as hidden_workflow\n",
        encoding="utf-8",
    )

    with pytest.raises(validation.InvalidWorkflow, match="must export"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_shadowed_entrypoint_export(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "__init__.py").write_text(
        "from .workflow import demo_workflow\n"
        "\n"
        "def demo_workflow(task):\n"
        "    return task\n",
        encoding="utf-8",
    )

    with pytest.raises(validation.InvalidWorkflow, match="without shadowing"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_import_shadowed_entrypoint_export(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "__init__.py").write_text(
        "from .workflow import demo_workflow\n"
        "from .steps.work import work as demo_workflow\n",
        encoding="utf-8",
    )

    with pytest.raises(validation.InvalidWorkflow, match="without shadowing"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_rebound_agentic_decorator(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "workflow.py").write_text(
        "from openprogram.agentic_programming import agentic_function\n"
        "from .steps.work import work\n"
        "\n"
        "def agentic_function(function):\n"
        "    return function\n"
        "\n"
        "@agentic_function\n"
        "def demo_workflow(task: str):\n"
        "    return work(task)\n",
        encoding="utf-8",
    )

    with pytest.raises(validation.InvalidWorkflow, match="cannot redefine managed"):
        validation.validate_workflow_directory(project)


def test_directory_validation_rejects_wildcard_import(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    with (project / "__init__.py").open("a", encoding="utf-8") as stream:
        stream.write("from .steps.work import *\n")

    with pytest.raises(validation.InvalidWorkflow, match="import is not allowed"):
        validation.validate_workflow_directory(project)


def test_legacy_reader_keeps_human_display_name_compatibility(
    tmp_path: Path,
) -> None:
    project = tmp_path / "literature-review"
    (project / "steps").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "literature-review"\n'
        'version = "0.1.0"\n'
        'description = "Historical literature review"\n'
        'keywords = []\n\n'
        "[tool.openprogram]\n"
        'display-name = "Literature Review"\n',
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Historical\n", encoding="utf-8")
    (project / "entry.py").write_text(
        "def workflow():\n    return work()\n",
        encoding="utf-8",
    )
    (project / "steps" / "work.py").write_text(
        "def work():\n    return 'done'\n",
        encoding="utf-8",
    )

    candidate = repository._read_repository_candidate(
        project,
        allow_legacy_entry=True,
        expected_project_id="literature-review",
    )

    assert candidate["project_metadata"]["name"] == "Literature Review"
    assert "entrypoint" not in candidate["project_metadata"]
