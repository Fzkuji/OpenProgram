"""Batch 2a coverage for agentics runtime.exec migration."""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

MIGRATED_FUNCTIONS = {
    "openprogram.functions.agentics.deep_work": ("_clarify", "_evaluate"),
    "openprogram.functions.agentics.extract_pdf_figures": ("extract_pdf_figures",),
    "openprogram.functions.agentics.extract_pdf_tables": ("extract_pdf_tables",),
    "openprogram.functions.agentics.llm_call_example": (
        "summarize_text",
        "translate_to_chinese",
        "polish_text",
    ),
    "openprogram.functions.agentics.research.evaluate": ("_evaluate_candidates",),
    "openprogram.functions.agentics.research.stages.idea": (
        "generate_ideas",
        "check_novelty",
        "rank_ideas",
    ),
    "openprogram.functions.agentics.research.stages.literature": (
        "survey_topic",
        "identify_gaps",
    ),
    "openprogram.functions.agentics.research.stages.experiment": (
        "design_experiments",
        "run_experiment",
        "check_training",
    ),
    "openprogram.functions.agentics.research.stages.writing": (
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
    "openprogram.functions.agentics.research.stages.review": (
        "review_paper",
        "fix_paper",
    ),
    "openprogram.functions.agentics.research.stages.submission": (
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
        "openprogram.functions.agentics.llm_call_example"
    )
    monkeypatch.setattr(module, "llm", fake_llm)

    assert module.summarize_text("source text") == "summary"

    assert calls == [([{
        "type": "text",
        "text": "Please summarize:\n\nsource text",
    }], {})]


def test_only_deferred_tool_loops_still_call_runtime_exec():
    root = Path(__file__).parents[2] / "openprogram" / "functions" / "agentics"
    excluded = {
        "Research-Agent-Harness",
        "GUI-Agent-Harness",
        "Wiki-Agent-Harness",
        "task_list",
    }
    remaining = []
    for path in root.rglob("*.py"):
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

    remaining.sort()
    assert [path for path, _ in remaining] == [
        "deep_work/__init__.py",
        "llm_call_example/__init__.py",
    ]
    for relative_path, line_number in remaining:
        lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
        nearby = "\n".join(lines[max(0, line_number - 3):line_number])
        assert "tool loop, migrates with agent() in a later batch" in nearby
