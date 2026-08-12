"""Batch 2a coverage for agentics runtime.exec migration."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

MIGRATED_FUNCTIONS = {
    "openprogram.functions.agentics.extract_pdf_figures": ("extract_pdf_figures",),
    "openprogram.functions.agentics.extract_pdf_tables": ("extract_pdf_tables",),
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


def test_agentics_do_not_call_runtime_exec():
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

    assert remaining == []
