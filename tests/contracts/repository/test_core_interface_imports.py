"""The core never imports an interface.

Every package under ``openprogram.interfaces`` (openprogram/interfaces: the
Server, the CLI, ACP, the chat channels, speech) is built on the core; the
core reaches them only through ports an interface registers (for example
``openprogram.host.resources.desktop_link``,
``openprogram.runtime.interface_link``, ``openprogram.runtime.channel_link``
and ``openprogram.runtime.speech_link``). Only the composition roots that
start an interface may import one.

The scan reads every module under ``openprogram/`` outside
``openprogram/interfaces/`` as source, so it also sees imports inside
functions and string imports through ``importlib.import_module`` and
``__import__``.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "openprogram"
INTERFACES = ("openprogram.interfaces",)

# Composition roots: they exist to start an interface.
COMPOSITION_ROOTS = frozenset({
    # ``python -m openprogram`` runs the CLI application.
    "openprogram/__main__.py",
    # The worker process starts the Server, its runtime and its frontend.
    "openprogram/operations/worker/runner.py",
})

def _names_interface(name: object) -> bool:
    return isinstance(name, str) and any(
        name == prefix or name.startswith(prefix + ".") for prefix in INTERFACES
    )


def _interface_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"{node.lineno}: import {a.name}" for a in node.names if _names_interface(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if _names_interface(node.module):
                found.append(f"{node.lineno}: from {node.module} import …")
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
                and _names_interface(first.value)
            ):
                found.append(f"{node.lineno}: {callee}({first.value!r})")
    return found


def _core_violations() -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    for path in sorted(CORE.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("openprogram/interfaces/") or "__pycache__" in relative:
            continue
        found = _interface_imports(path)
        if found:
            violations[relative] = found
    return violations


def test_core_modules_do_not_import_an_interface():
    violations = {
        path: found for path, found in _core_violations().items()
        if path not in COMPOSITION_ROOTS
    }
    assert violations == {}, (
        "core modules import an interface; reach it through a port it registers:\n"
        + "\n".join(f"{path}\n  " + "\n  ".join(found) for path, found in violations.items())
    )


def test_the_scan_sees_function_local_and_string_imports(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import importlib\n"
        "def f():\n"
        "    from openprogram.interfaces.server import server\n"
        "    importlib.import_module('openprogram.interfaces.cli')\n"
        "    __import__('openprogram.interfaces.server')\n"
        "    from openprogram.interfaces import cli\n"
        "    from openprogram.interfaces.channels import outbound\n"
        "    import openprogram.interfaces.speech\n",
        encoding="utf-8",
    )
    assert len(_interface_imports(sample)) == 6
