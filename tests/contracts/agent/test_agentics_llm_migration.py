"""Batch 2a coverage for agentics runtime.exec migration."""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from tests.support.repository import tracked_python_files

MIGRATED_FUNCTIONS = {
    "openprogram.programs.functions.agentic.deep_work": ("_clarify", "_evaluate"),
    "openprogram.programs.functions.agentic.extract_pdf_figures": ("extract_pdf_figures",),
    "openprogram.programs.functions.agentic.extract_pdf_tables": ("extract_pdf_tables",),
    "openprogram.programs.functions.agentic.llm_call_example": (
        "summarize_text",
        "translate_to_chinese",
        "polish_text",
    ),
    "openprogram.programs.functions.agentic.research.evaluate": ("_evaluate_candidates",),
    "openprogram.programs.functions.agentic.research.stages.idea": (
        "generate_ideas",
        "check_novelty",
        "rank_ideas",
    ),
    "openprogram.programs.functions.agentic.research.stages.literature": (
        "survey_topic",
        "identify_gaps",
    ),
    "openprogram.programs.functions.agentic.research.stages.experiment": (
        "design_experiments",
        "run_experiment",
        "check_training",
    ),
    "openprogram.programs.functions.agentic.research.stages.writing": (
        "write_section",
        "translate_zh2en",
        "translate_en2zh",
        "polish_rigorous",
        "polish_natural",
        "check_logic",
        "analyze_results",
        "compress_text",
        "expand_text",
    ),
    "openprogram.programs.functions.agentic.research.stages.review": (
        "review_paper",
        "fix_paper",
    ),
    "openprogram.programs.functions.agentic.research.stages.submission": (
        "check_submission",
    ),
}


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        (module_name, function_name)
        for module_name, function_names in MIGRATED_FUNCTIONS.items()
        for function_name in function_names
    ],
)
def test_migrated_functions_do_not_thread_runtime(module_name, function_name):
    function = getattr(importlib.import_module(module_name), function_name)

    assert "runtime" not in inspect.signature(function._fn).parameters


def test_migrated_function_passes_content_blocks_to_llm(monkeypatch):
    calls = []

    def fake_llm(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "summary"

    module = importlib.import_module(
        "openprogram.programs.functions.agentic.llm_call_example"
    )
    monkeypatch.setattr(module, "llm", fake_llm)

    assert module.summarize_text("source text") == "summary"

    assert calls == [([{
        "type": "text",
        "text": "Please summarize:\n\nsource text",
    }], {})]


def test_only_deferred_tool_loops_still_call_runtime_exec():
    root = (
        Path(__file__).parents[3]
        / "packages"
        / "core"
        / "src"
        / "openprogram"
        / "programs"
        / "functions"
        / "agentic"
    )
    excluded = {
        "Research-Agent-Harness",
        "GUI-Agent-Harness",
        "Wiki-Agent-Harness",
        "agentic_workflow",
    }
    remaining = []
    for path in tracked_python_files(root):
        if excluded.intersection(path.relative_to(root).parts):
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "runtime"
                and node.func.attr == "exec"
            ):
                remaining.append((path.relative_to(root).as_posix(), node.lineno))

    expected_paths = [
        "browser_agent/__init__.py",
        "browser_agent/__init__.py",
        "deep_work/__init__.py",
        "llm_call_example/__init__.py",
    ]
    expected_markers = {
        "browser_agent/__init__.py": "deferred browser tool loop",
        "deep_work/__init__.py": "tool loop, migrates with agent() in a later batch",
        "llm_call_example/__init__.py": (
            "tool loop, migrates with agent() in a later batch"
        ),
    }
    remaining.sort()
    assert [path for path, _ in remaining] == sorted(expected_paths)
    for relative_path, line_number in remaining:
        lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
        nearby = "\n".join(lines[max(0, line_number - 3):line_number])
        assert expected_markers[relative_path] in nearby
