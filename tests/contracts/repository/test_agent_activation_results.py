"""Every caller of the canonical Agent activation handles a turn that never ran.

``CanonicalAgentAdapter.activate`` returns ``(activation, result)`` for a turn
that ran and ``None`` for one a stop or a pause reached before it started.
Unpacking the call straight into a tuple turns that stop into a ``TypeError``,
so the scan rejects ``a, b = asyncio.run(adapter.activate(...))`` and
``a, b = await adapter.activate(...)`` anywhere in ``openprogram/``.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.support.repository import tracked_python_files

ROOT = Path(__file__).resolve().parents[3]


def _adapter_activation(node: ast.AST) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run" and node.args):
        node = node.args[0]
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "activate"
        and isinstance(node.func.value, ast.Name)
        and "adapter" in node.func.value.id.lower()
    )


def test_no_caller_unpacks_an_activation_that_may_not_have_run():
    found = []
    for path in tracked_python_files(ROOT / "openprogram"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and _adapter_activation(node.value)
                    and any(isinstance(target, ast.Tuple) for target in node.targets)):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert found == []
