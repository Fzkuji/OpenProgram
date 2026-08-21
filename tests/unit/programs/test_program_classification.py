"""Classification contract for Vanilla / Agentic Function / Workflow."""

from __future__ import annotations

from pathlib import Path

import openprogram
from openprogram.programs._registry import AGENTIC_MODULES
from openprogram.webui.routes.programs import _called_agentic_primitives


PROGRAMS = Path(openprogram.__file__).resolve().parent / "programs"
AGENTIC = PROGRAMS / "functions" / "agentic"
VANILLA = PROGRAMS / "functions" / "vanilla"

WORKFLOW_PLACEMENTS = {
    "browser": AGENTIC / "workflow" / "browser",
    "docs_question": AGENTIC / "workflow" / "docs_question",
    "security_review": AGENTIC / "workflow" / "security_review",
    "goal": AGENTIC / "workflow" / "goal",
    "deep_work": AGENTIC / "workflow" / "deep_work",
    "research": AGENTIC / "workflow" / "research",
    "authoring": AGENTIC / "workflow" / "authoring",
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
    pkg = AGENTIC.joinpath(*parts, "__init__.py")
    simple = AGENTIC.joinpath(*parts[:-1], f"{parts[-1]}.py") if len(parts) > 1 else AGENTIC / f"{mod_name}.py"
    if pkg.is_file():
        return pkg
    return simple


def test_complex_capabilities_live_under_workflow_not_agentic_root():
    for name, path in WORKFLOW_PLACEMENTS.items():
        assert path.is_dir(), f"{name} must live under functions/agentic/workflow/"
    for name in FORBIDDEN_PRODUCT_DIRS:
        leftover = AGENTIC / name
        assert not leftover.exists(), f"{name} must not remain at agentic root"


def test_agentic_modules_do_not_register_demos():
    assert "interaction_demo" not in AGENTIC_MODULES
    assert "llm_call_example" not in AGENTIC_MODULES
    assert "word_count" not in AGENTIC_MODULES
    assert "test_framework" not in AGENTIC_MODULES
    assert "test_resume" not in AGENTIC_MODULES


def test_vanilla_callables_do_not_reach_model_primitives():
    for source in VANILLA.rglob("*.py"):
        if source.name.startswith("_") or "__pycache__" in source.parts:
            continue
        called, _warnings = _called_agentic_primitives(source)
        assert not called, f"{source.relative_to(VANILLA)} reached {sorted(called)}"


def test_ordinary_agentic_functions_call_llm_once_and_not_agent_or_goal():
    ordinary = [
        name for name in AGENTIC_MODULES
        if name != "workflow"
        and not name.startswith("workflow.")
        and name.split(".", 1)[0] not in INFRASTRUCTURE
        and name not in INFRASTRUCTURE
    ]
    assert ordinary, "expected document/text agentic functions"
    for name in ordinary:
        source = _module_path(name)
        assert source.is_file(), name
        called, warnings = _called_agentic_primitives(
            source.parent if source.name == "__init__.py" else source,
        )
        assert "source_parse_failed" not in warnings, name
        assert "agent" not in called, name
        assert "goal" not in called, name
        assert called == {"llm"}, f"{name} primitives={sorted(called)}"


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
    assert not (AGENTIC / "runtime.py").exists()
    assert not (AGENTIC / "legacy.py").exists()
