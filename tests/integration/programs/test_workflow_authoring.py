from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from openprogram import cli, paths, sandbox
from openprogram.programs.workflow._project import catalog


def _package(root: Path, *, passing: bool = True) -> Path:
    project = root / "manual_report"
    (project / "steps").mkdir(parents=True)
    (project / "tests").mkdir()
    files = {
        "pyproject.toml": catalog._project_pyproject("manual_report", {
            "name": "manual_report", "summary": "Manual workflow", "tags": [], "entrypoint": "manual_report",
        }),
        "README.md": "# Manual report\n",
        "__init__.py": "from .workflow import manual_report\n",
        "workflow.py": "from openprogram.agentic_programming import agentic_function\nfrom .steps.prepare import prepare\n@agentic_function\ndef manual_report(task: str):\n    return prepare(task)\n",
        "steps/prepare.py": "def prepare(task: str):\n    return task + '!'\n",
        "tests/test_workflow.py": "from workflows.manual_report import manual_report\ndef test_output():\n    assert manual_report.__wrapped__('example') == " + ("'example!'\n" if passing else "'wrong'\n"),
    }
    for name, value in files.items():
        (project / name).write_text(value, encoding="utf-8")
    return project


def _invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["openprogram", "workflows", *args, "--json"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return stopped.value.code, json.loads(captured.out)


@pytest.mark.sandbox
def test_manual_test_and_publish_use_a_sandboxed_snapshot(tmp_path, monkeypatch, capsys):
    if sys.platform not in {"darwin", "linux"} or sandbox.unavailable_reason():
        pytest.skip("OS behavior-test sandbox is unavailable")
    project = _package(tmp_path / "authored")
    before = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}
    published = tmp_path / "catalog" / "workflow"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: published)
    code, report = _invoke(monkeypatch, capsys, "test", str(project))
    assert code == 0, report
    assert report["executed_tests"] and report["sandboxed"]
    assert not published.exists()
    assert {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()} == before
    code, report = _invoke(monkeypatch, capsys, "publish", str(project))
    assert code == 0, report
    assert len(report["revision"]) == 40
    assert (published / "manual_report" / "workflow.py").read_bytes() == before[Path("workflow.py")]
    code, report = _invoke(monkeypatch, capsys, "publish", str(project))
    assert code == 1
    assert "already exists" in report["error"]
    (project / "steps" / "prepare.py").write_text("def prepare(task: str):\n    return task + '!!'\n")
    test_file = project / "tests" / "test_workflow.py"
    test_file.write_text(test_file.read_text().replace("example!", "example!!"))
    code, report = _invoke(monkeypatch, capsys, "publish", str(project), "--replace")
    assert code == 0, report
    assert "!!" in (published / "manual_report" / "steps" / "prepare.py").read_text()
    destination_readme = published / "manual_report" / "README.md"
    destination_readme.write_text("Uncommitted owner edit\n")
    code, report = _invoke(monkeypatch, capsys, "publish", str(project), "--replace")
    assert code == 1
    assert "uncommitted changes" in report["error"]
    assert destination_readme.read_text() == "Uncommitted owner edit\n"


@pytest.mark.sandbox
def test_failed_behavior_test_publishes_nothing(tmp_path, monkeypatch, capsys):
    if sys.platform not in {"darwin", "linux"} or sandbox.unavailable_reason():
        pytest.skip("OS behavior-test sandbox is unavailable")
    project = _package(tmp_path / "authored", passing=False)
    published = tmp_path / "catalog" / "workflow"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: published)
    code, report = _invoke(monkeypatch, capsys, "publish", str(project))
    assert code == 1
    assert "tests failed" in report["error"]
    assert not published.exists()


def test_unavailable_sandbox_never_publishes(tmp_path, monkeypatch, capsys):
    project = _package(tmp_path / "authored")
    published = tmp_path / "catalog" / "workflow"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: published)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "test backend unavailable")
    code, report = _invoke(monkeypatch, capsys, "publish", str(project))
    assert code == 1
    assert "sandbox" in report["error"]
    assert not published.exists()


@pytest.mark.sandbox
def test_behavior_test_timeout_never_publishes(tmp_path, monkeypatch, capsys):
    if sys.platform not in {"darwin", "linux"} or sandbox.unavailable_reason():
        pytest.skip("OS behavior-test sandbox is unavailable")
    from openprogram.programs.workflow._project import authoring

    project = _package(tmp_path / "authored")
    published = tmp_path / "catalog" / "workflow"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: published)
    monkeypatch.setattr(authoring, "_TEST_TIMEOUT", 0.1)
    code, report = _invoke(monkeypatch, capsys, "publish", str(project))
    assert code == 1
    assert "exceeded" in report["error"]
    assert not published.exists()


@pytest.mark.sandbox
def test_behavior_sandbox_protects_host_and_source(tmp_path, monkeypatch):
    if sys.platform not in {"darwin", "linux"} or sandbox.unavailable_reason():
        pytest.skip("OS behavior-test sandbox is unavailable")
    import socket
    from openprogram.programs.workflow._project import authoring

    instance = tmp_path / "candidate"
    tests = instance / "snapshot" / "workflows" / "probe" / "tests"
    tests.mkdir(parents=True)
    secret = tmp_path / ".env"
    secret.write_text("not a real credential")
    outside = tmp_path / "outside.txt"
    frozen = tests.parent / "source.txt"
    frozen.write_text("original")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-must-not-reach-child")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        (tests / "test_policy.py").write_text(
            "import os\nimport socket\nfrom pathlib import Path\nimport pytest\n"
            "def test_policy():\n"
            "    assert 'OPENAI_API_KEY' not in os.environ\n"
            f"    with pytest.raises(OSError):\n        Path({str(secret)!r}).read_text()\n"
            f"    with pytest.raises(OSError):\n        Path({str(outside)!r}).write_text('escaped')\n"
            f"    with pytest.raises(OSError):\n        Path({str(frozen)!r}).write_text('changed')\n"
            f"    with pytest.raises(OSError):\n        socket.create_connection(('127.0.0.1', {port}), timeout=0.2)\n"
        )
        report = authoring._run_tests(instance, "probe")
    assert report["sandboxed"] and report["executed_tests"]
    assert not outside.exists()
    assert frozen.read_text() == "original"


@pytest.mark.sandbox
def test_generated_candidate_uses_the_same_publication_gate(tmp_path, monkeypatch):
    if sys.platform not in {"darwin", "linux"} or sandbox.unavailable_reason():
        pytest.skip("OS behavior-test sandbox is unavailable")
    from openprogram.programs.workflow._project import repository
    from openprogram.programs.workflow.errors import InvalidWorkflow

    project = _package(tmp_path / "authored", passing=False)
    candidate = repository._read_repository_candidate(project, expected_project_id="manual_report")
    published = tmp_path / "catalog" / "workflow"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(catalog, "_workflow_projects_root", lambda: published)
    with pytest.raises(InvalidWorkflow, match="behavior tests failed"):
        repository._publish_candidate(candidate, project_id="", action="create")
    assert not published.exists()


@pytest.mark.sandbox
def test_macos_behavior_tests_cannot_leave_detached_processes(tmp_path, monkeypatch, capsys):
    if sys.platform != "darwin" or sandbox.unavailable_reason():
        pytest.skip("Requires macOS Seatbelt")
    project = _package(tmp_path / "authored")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    (project / "tests" / "test_workflow.py").write_text(
        "def test_detached_process():\n"
        "    import subprocess\n    import pytest\n"
        "    with pytest.raises(OSError):\n"
        "        subprocess.Popen(['/bin/sleep', '20'], start_new_session=True)\n"
    )
    code, report = _invoke(monkeypatch, capsys, "test", str(project))
    assert code == 0, report
    assert report["executed_tests"]
