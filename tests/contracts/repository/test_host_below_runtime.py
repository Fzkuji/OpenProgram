"""The host layer sits below the runtime and Programs and never imports them.

``openprogram.host`` — the machine the agent runs on: backends, environments,
the sandbox, managed processes, resources, system access, the platform
bindings — is what the runtime and Programs are built on. When a host module
needs a runtime fact (the bound session and execution, whether the worker is
stopping, the execution store) or a Program's service, the upper layer hands
it down: ``state.execution_facts`` and ``state.event_sink`` already carry the
session facts and the event bus, and a port the host owns carries the rest,
filled by ``runtime.lower_layers`` when the runtime loads. Code that is really
a runtime or Program feature does not live in ``host/`` at all. There are no
exceptions, at module level or inside a function.

The scan reads every module under ``openprogram/host`` as source, so it also
sees imports inside functions, relative imports that climb out of the
package, and string imports through ``importlib.import_module`` and
``__import__``. Imports that break the rule today are listed in ``ALLOWED``;
the test fails on a new one and on an entry that no longer occurs, so the
list only shrinks.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "openprogram"
HOST = "openprogram/host"
UPPER = ("openprogram.runtime", "openprogram.programs", "openprogram.extensions")

# (file, imported module) pairs that break the rule today. Remove an entry
# when its import goes away; never add one.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
})


def _within(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    """Every module ``path`` imports, resolved to absolute names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            found.add(source)
            # ``from openprogram.runtime import interface_link`` imports a module.
            found.update(f"{source}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            first = node.args[0]
            if callee in {"import_module", "__import__"} and isinstance(first, ast.Constant):
                if isinstance(first.value, str):
                    found.add(first.value)
    return found


def _scan() -> dict[tuple[str, str], str]:
    paths = [p for p in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]
    modules = {_module_name(p) for p in paths}
    violations: dict[tuple[str, str], str] = {}
    for path in sorted((ROOT / HOST).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        # Keep the names that are modules (drop ``package.attribute`` guesses).
        for name in sorted(_imported_modules(path) & modules):
            if _within(name, UPPER):
                violations[(relative, name)] = HOST
    return violations


def test_host_does_not_import_the_runtime_or_programs():
    violations = _scan()
    new = sorted(pair for pair in violations if pair not in ALLOWED)
    stale = sorted(pair for pair in ALLOWED if pair not in violations)
    assert new == [], (
        "host imports the runtime or Programs; have the upper layer hand it down "
        "through a port it fills when it loads, or move the module up (do not allow it):\n"
        + "\n".join(f"  {file}: {name}" for file, name in new)
    )
    assert stale == [], "these allowed imports are gone; delete them from ALLOWED:\n" + "\n".join(
        f"  {pair!r}," for pair in stale
    )
