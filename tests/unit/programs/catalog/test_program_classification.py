"""Classification contract for Vanilla / Agentic Function / Workflow."""

from __future__ import annotations

from pathlib import Path

import openprogram
from openprogram.programs._host.registry import TOOL_PROGRAMS, builtin_program_modules
from openprogram.interfaces.server.routes.catalog.programs import _called_agentic_primitives


PROGRAMS = Path(openprogram.__file__).resolve().parent / "programs"
APPLICATIONS = PROGRAMS / "applications"
TOOLS = PROGRAMS / "tools"

PROGRAM_PLACEMENTS = {
    "browser": APPLICATIONS / "browser",
    "workflow_recording": APPLICATIONS / "workflow_recording",
    "goal": TOOLS / "planning" / "goal",
}

# The former authoring tools are gone: workflows are written as files and
# checked with `openprogram programs check`.
REMOVED_WORKFLOW_ENTRIES = (
    "search_workflows", "create_workflow", "revise_workflow",
    "resume_workflow", "auto_workflow",
)

FORBIDDEN_PRODUCT_DIRS = (
    "browser_agent",
    "deep_work",
    "interaction_demo",
    "llm_call_example",
    "word_count",
    "test_framework",
    "test_resume",
    "agentic_workflow",
)


def test_programs_live_under_tools_and_applications():
    assert not (PROGRAMS / "workflow").exists(), "the workflow layer is gone"
    for name, path in PROGRAM_PLACEMENTS.items():
        assert path.is_dir(), f"{name} must live at {path.relative_to(PROGRAMS)}"
    assert (TOOLS / "interaction" / "ask_user.py").is_file()
    modules = builtin_program_modules()
    for name in REMOVED_WORKFLOW_ENTRIES:
        assert name not in modules, name
    for name in FORBIDDEN_PRODUCT_DIRS:
        assert not (APPLICATIONS / name).exists(), name
        assert name not in modules, name


def test_builtin_programs_declare_program_entries_or_a_setup():
    """A tool package registers entries; an application's entries are optional
    when its ``setup`` provides what it offers (``workflow_recording``)."""
    import importlib

    # A tool always has an entry; an application may have none (it then
    # registers what it offers in ``setup``, like ``office_editor``'s service).
    for name, module_path in builtin_program_modules().items():
        module = importlib.import_module(module_path)
        if name in TOOL_PROGRAMS:
            assert getattr(module, "PROGRAM_ENTRIES", None), name
        else:
            assert getattr(module, "PROGRAM_ENTRIES", None) or callable(getattr(module, "setup", None)), name


def test_vanilla_callables_do_not_reach_model_primitives():
    for source in TOOLS.rglob("*.py"):
        if source.name.startswith("_") or "__pycache__" in source.parts:
            continue
        called, _warnings = _called_agentic_primitives(source)
        assert not called, f"{source.relative_to(TOOLS)} reached {sorted(called)}"


def test_tool_programs_are_tool_packages():
    import importlib

    for name, module_path in TOOL_PROGRAMS.items():
        assert module_path.startswith("openprogram.programs.tools."), name
        source = Path(importlib.import_module(module_path).__file__)
        assert TOOLS in source.parents, name


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
    assert not (PROGRAMS / "workflow").exists()
