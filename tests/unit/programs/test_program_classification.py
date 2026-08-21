"""Classification contract for Vanilla / Agentic Function / Workflow."""

from __future__ import annotations

from pathlib import Path

import openprogram
from openprogram.programs._registry import AGENTIC_MODULES, BUILTIN_WORKFLOW_MODULES
from openprogram.webui.routes.programs import _called_agentic_primitives


PROGRAMS = Path(openprogram.__file__).resolve().parent / "programs"
WORKFLOW = PROGRAMS / "workflow"
TOOLS = PROGRAMS / "tools"

WORKFLOW_PLACEMENTS = {
    "browser": WORKFLOW / "browser",
    "docs_question": WORKFLOW / "docs_question",
    "security_review": WORKFLOW / "security_review",
    "goal": WORKFLOW / "goal",
    "research": WORKFLOW / "research",
}

WORKFLOW_ENTRY_FILES = {
    "search_workflows": WORKFLOW / "search_workflows.py",
    "create_workflow": WORKFLOW / "create_workflow.py",
    "revise_workflow": WORKFLOW / "revise_workflow.py",
    "resume_workflow": WORKFLOW / "resume_workflow.py",
}

FORBIDDEN_PRODUCT_DIRS = (
    "browser_agent",
    "docs_question",
    "security_review",
    "goal",
    "deep_work",
    "research",
    "interaction_demo",
    "llm_call_example",
    "word_count",
    "test_framework",
    "test_resume",
    "agentic_workflow",
)

INFRASTRUCTURE = {"ask_user", "json_parsing"}


def _module_path(mod_name: str) -> Path:
    parts = mod_name.split(".")
    pkg = WORKFLOW.joinpath(*parts, "__init__.py")
    simple = WORKFLOW.joinpath(*parts[:-1], f"{parts[-1]}.py") if len(parts) > 1 else WORKFLOW / f"{mod_name}.py"
    if pkg.is_file():
        return pkg
    return simple


def test_complex_capabilities_live_under_workflow_not_agentic_root():
    for name, path in WORKFLOW_PLACEMENTS.items():
        assert path.is_dir(), f"{name} must live under workflow/"
    for name, path in WORKFLOW_ENTRY_FILES.items():
        assert path.is_file(), f"{name} must be a top-level workflow callable"
    assert not (WORKFLOW / "authoring").exists()
    for name in FORBIDDEN_PRODUCT_DIRS:
        leftover = WORKFLOW / name
        if name not in WORKFLOW_PLACEMENTS:
            assert not leftover.exists(), f"{name} must not remain as a duplicate"


def test_auto_workflow_is_a_builtin_workflow_not_an_agentic_helper():
    assert (PROGRAMS / "workflow" / "auto_workflow.py").is_file()
    assert "auto_workflow" in AGENTIC_MODULES
    assert BUILTIN_WORKFLOW_MODULES == []


def test_agentic_modules_do_not_register_demos():
    assert "interaction_demo" not in AGENTIC_MODULES
    assert "llm_call_example" not in AGENTIC_MODULES
    assert "word_count" not in AGENTIC_MODULES
    assert "test_framework" not in AGENTIC_MODULES
    assert "test_resume" not in AGENTIC_MODULES


def test_vanilla_callables_do_not_reach_model_primitives():
    for source in TOOLS.rglob("*.py"):
        if source.name.startswith("_") or "__pycache__" in source.parts:
            continue
        called, _warnings = _called_agentic_primitives(source)
        assert not called, f"{source.relative_to(TOOLS)} reached {sorted(called)}"


def test_registered_workflow_modules_have_source_files():
    for name in AGENTIC_MODULES:
        source = _module_path(name)
        assert source.is_file(), name


def test_first_party_shipped_skills_inventory_is_empty():
    skills_pkg = Path(openprogram.__file__).resolve().parent / "skills"
    shipped = [
        path for path in skills_pkg.rglob("SKILL.md")
        if "tests" not in path.parts
    ] if skills_pkg.is_dir() else []
    assert shipped == []


def test_no_workflow_decorator_or_second_runtime():
    root = Path(openprogram.__file__).resolve().parent
    hits = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "@workflow" in text or "def call_workflow(" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []
    assert not (WORKFLOW / "runtime.py").exists()
    assert not (WORKFLOW / "legacy.py").exists()
