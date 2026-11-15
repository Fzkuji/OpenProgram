"""Models and state sit below the runtime and never import it.

``openprogram.models`` (providers, auth, usage) and ``openprogram.state``
(stores, memory, settings) are what the runtime is built on. When they need
something only the runtime has, the runtime hands it down: a parameter
from its caller, a hook or port it fills in when it loads
(``state.memory.writer_agent``,
``state.store.current``, ``state.execution_facts``, ``state.event_sink``;
see ``runtime.lower_layers``), or a table it owns
(``runtime.agent.provider_runtimes``). There are no exceptions, at module
level or inside a function.

The scan reads every module under ``openprogram/models`` and
``openprogram/state`` as source, so it also sees imports inside functions,
relative imports that climb out of the package, and string imports through
``importlib.import_module`` and ``__import__``.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOWER_LAYERS = ("openprogram/models", "openprogram/state")
RUNTIME = "openprogram.runtime"


def _names_runtime(name: object) -> bool:
    return isinstance(name, str) and (name == RUNTIME or name.startswith(RUNTIME + "."))


def _absolute(package: list[str], node: ast.ImportFrom) -> str:
    """The absolute module an import names, resolving ``from ..x import``."""
    if node.level == 0:
        return node.module or ""
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _nodes(tree: ast.AST, *, module_level_only: bool):
    """Every node, or only those that run when the module is imported."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if module_level_only and isinstance(child, _FUNCTIONS):
                continue
            stack.append(child)


def _runtime_imports(
    path: Path, root: Path = ROOT, *, module_level_only: bool = False,
) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = list(path.relative_to(root).parent.parts)
    found: list[str] = []
    for node in _nodes(tree, module_level_only=module_level_only):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if _names_runtime(a.name)]
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(package, node)
            if _names_runtime(module):
                found.append(f"from {module} import …")
            elif module == "openprogram" and any(a.name == "runtime" for a in node.names):
                found.append("from openprogram import runtime")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            first = node.args[0]
            if (
                callee in {"import_module", "__import__"}
                and isinstance(first, ast.Constant)
                and _names_runtime(first.value)
            ):
                found.append(f"{callee}({first.value!r})")
    return found


def _violations(*, module_level_only: bool = False) -> dict[str, frozenset[str]]:
    violations: dict[str, frozenset[str]] = {}
    for layer in LOWER_LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found = _runtime_imports(path, module_level_only=module_level_only)
            if found:
                violations[path.relative_to(ROOT).as_posix()] = frozenset(found)
    return violations


def test_no_module_imports_the_runtime_when_it_loads():
    """No exceptions at module level: loading models or state never loads the runtime."""
    violations = _violations(module_level_only=True)
    assert violations == {}, (
        "models/state import the runtime at module level:\n"
        + "\n".join(f"{path}\n  " + "\n  ".join(sorted(found)) for path, found in violations.items())
    )


def test_models_and_state_do_not_import_the_runtime():
    """No exceptions inside functions either."""
    violations = _violations()
    assert violations == {}, (
        "models/state import the runtime; have the runtime pass it down "
        "(a parameter, a registered hook or port, a table it owns):\n"
        + "\n".join(
            f"{path}\n  " + "\n  ".join(sorted(found))
            for path, found in violations.items()
        )
    )


def test_the_scan_sees_function_local_relative_and_string_imports(tmp_path):
    package = tmp_path / "openprogram" / "state" / "sample"
    package.mkdir(parents=True)
    sample = package / "module.py"
    sample.write_text(
        "import importlib\n"
        "def f():\n"
        "    from openprogram.runtime.context import nodes\n"
        "    from ...runtime.events import emit\n"
        "    from openprogram import runtime\n"
        "    import openprogram.runtime.agent\n"
        "    importlib.import_module('openprogram.runtime.agent')\n"
        "    __import__('openprogram.runtime')\n"
        "    from ..store import session\n",
        encoding="utf-8",
    )
    assert len(_runtime_imports(sample, root=tmp_path)) == 6
    assert _runtime_imports(sample, root=tmp_path, module_level_only=True) == []

    sample.write_text(
        "try:\n"
        "    from openprogram.runtime.context.nodes import Call\n"
        "except ImportError:\n"
        "    Call = None\n",
        encoding="utf-8",
    )
    assert len(_runtime_imports(sample, root=tmp_path, module_level_only=True)) == 1
