"""Programs call models through roles, never through ``runtime.exec``."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.repository import tracked_python_files


def test_programs_do_not_call_runtime_exec():
    root = Path(__file__).parents[3] / "openprogram" / "programs"
    scanned = 0
    remaining = []
    for path in tracked_python_files(root):
        scanned += 1
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "exec"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id.endswith("runtime")
            ):
                remaining.append((path.relative_to(root).as_posix(), node.lineno))

    assert scanned > 100
    assert remaining == []


def test_builtin_programs_use_roles_not_agent_subclasses():
    """Built-in programs define module-level roles and plain functions."""
    root = Path(__file__).parents[3] / "openprogram" / "programs"
    subclasses = []
    for path in tracked_python_files(root):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                getattr(base, "id", getattr(base, "attr", None)) == "Agent"
                for base in node.bases
            ):
                subclasses.append((path.relative_to(root).as_posix(), node.name))
    assert subclasses == []
