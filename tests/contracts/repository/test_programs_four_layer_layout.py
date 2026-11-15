"""Public layout contract for shipped Programs source code.

``openprogram/programs/`` holds the public API (``__init__.py``), its README
and four directories: ``tools/`` and ``applications/`` (the built-in
Programs), ``_authoring/`` (what Program code imports) and ``_host/`` (how
the host loads and runs Programs). Nothing a user installs lives here.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

import openprogram.programs as programs_package
from openprogram.programs._host import registry

ROOT = Path(programs_package.__file__).resolve().parent

PACKAGE_ROOT = {"__init__.py", "README.md", "tools", "applications", "_authoring", "_host"}
# Interpreter caches and Finder metadata are not source.
NOT_SOURCE = {"__pycache__", ".DS_Store"}

AUTHORING = {
    "__init__.py", "json_parsing.py", "reports", "helpers.py",
    "providers.py", "guides.py", "prechecks.py", "application_client.py", "testing.py",
}
HOST = {
    "__init__.py", "runtime.py", "execution.py", "context.py", "services.py",
    "registry.py", "packages.py", "source_loader.py", "sources.py", "check.py",
    "watcher.py", "meta_storage.py", "identity.py", "lower_layers.py", "tool_rank.py", "ui", "framework",
}
HOST_UI = {
    "__init__.py", "bindings.py", "catalog.py", "catalog_store.py", "driver.py",
    "loader.py", "resource_provider.py", "runner.py", "scaffold.py", "service.py",
    "state.py",
}


def _entries(directory: Path) -> set[str]:
    return {child.name for child in directory.iterdir()} - NOT_SOURCE


def test_the_package_root_holds_only_the_api_and_four_directories() -> None:
    assert _entries(ROOT) == PACKAGE_ROOT


@pytest.mark.parametrize(
    ("relative", "expected"),
    [("_authoring", AUTHORING), ("_host", HOST), ("_host/ui", HOST_UI)],
)
def test_authoring_and_host_hold_their_modules(relative: str, expected: set[str]) -> None:
    assert _entries(ROOT / relative) == expected


@pytest.mark.parametrize(
    "removed",
    [
        "_runtime", "_registry", "_context", "_services", "_packages", "_programs",
        "_check", "_helpers", "_providers", "_source_loader", "_execution_common",
        "_applications", "guides", "prechecks", "watcher", "meta_storage",
        "application_client", "permission_rule", "packages", "agentic_functions",
        "workflow",
    ],
)
def test_former_module_paths_are_gone(removed: str) -> None:
    assert importlib.util.find_spec(f"openprogram.programs.{removed}") is None


def test_the_permission_model_is_its_own_package() -> None:
    from openprogram.runtime.permissions.rule import parse_rule, rule_to_string

    assert rule_to_string(parse_rule("bash(git:*)")) == "bash(git:*)"


def test_authoring_import_paths_used_by_user_programs() -> None:
    from openprogram.programs._authoring.json_parsing import parse_json

    assert parse_json('{"ok": true}') == {"ok": True}
    for module in ("reports.io", "reports.evidence_gate", "guides", "helpers"):
        importlib.import_module(f"openprogram.programs._authoring.{module}")


def test_builtin_programs_load_from_tools_and_applications() -> None:
    modules = registry.builtin_program_modules()

    assert modules["goal"] == "openprogram.programs.tools.planning.goal"
    assert modules["browser"] == "openprogram.programs.applications.browser"
    assert modules["workflow_recording"] == "openprogram.programs.applications.workflow_recording"
    assert modules["office_editor"] == "openprogram.programs.applications.office_editor"
    assert modules["meeting"] == "openprogram.programs.applications.meeting"
    assert registry.builtin_application_names() == sorted(["browser", "meeting", "office_editor", "workflow_recording"])


def test_applications_holds_only_built_in_applications() -> None:
    tracked = {
        child.name for child in (ROOT / "applications").iterdir()
        if child.is_dir() and child.name not in NOT_SOURCE and not (child / ".git").exists()
    }
    assert tracked == set(registry.builtin_application_names())


def test_the_core_reaches_workflow_recording_only_through_its_service() -> None:
    """No module outside the application imports it; the core looks up the service."""
    repo = ROOT.parents[1]
    application = ROOT / "applications" / "workflow_recording"
    sources = list(repo.joinpath("openprogram").rglob("*.py"))
    importers = [
        str(path.relative_to(repo)) for path in sources
        if not path.is_relative_to(application)
        and "applications.workflow_recording" in path.read_text(encoding="utf-8")
    ]
    assert importers == []
