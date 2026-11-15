"""State and models sit below Programs and extensions and never import them.

``openprogram.state`` (stores, memory, settings) and ``openprogram.models``
(providers, auth, usage) are what Programs and extensions are built on. When
they need something only a Program has — the registered web-search backends
for a settings choice list, the Goal tool's budget record for usage metering,
the browser's argument normaliser — the upper layer hands it down through a
port the lower layer owns and the upper layer fills when it loads, exactly as
the runtime does in ``runtime.lower_layers``. There are no exceptions, at
module level or inside a function.

The scan reads every module under ``openprogram/state`` and
``openprogram/models`` as source, so it also sees imports inside functions,
relative imports that climb out of the package, and string imports through
``importlib.import_module`` and ``__import__``. Imports that break the rule
today are listed in ``ALLOWED``; the test fails on a new one and on an entry
that no longer occurs, so the list only shrinks.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "openprogram"
LOWER_LAYERS = ("openprogram/state", "openprogram/models")
UPPER = ("openprogram.programs", "openprogram.extensions")

# (file, imported module) pairs that break the rule today. Remove an entry
# when its import goes away; never add one.
ALLOWED: frozenset[tuple[str, str]] = frozenset({})


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
            # ``from openprogram.programs import tools`` imports a package.
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
    for layer in LOWER_LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            # Keep the names that are modules (drop ``package.attribute`` guesses).
            for name in sorted(_imported_modules(path) & modules):
                if _within(name, UPPER):
                    violations[(relative, name)] = layer
    return violations


def test_state_and_models_do_not_import_programs_or_extensions():
    violations = _scan()
    new = sorted(pair for pair in violations if pair not in ALLOWED)
    stale = sorted(pair for pair in ALLOWED if pair not in violations)
    assert new == [], (
        "state/models import Programs or extensions; have the upper layer hand it "
        "down through a port it fills when it loads (do not allow it):\n"
        + "\n".join(f"  {file}: {name}" for file, name in new)
    )
    assert stale == [], "these allowed imports are gone; delete them from ALLOWED:\n" + "\n".join(
        f"  {pair!r}," for pair in stale
    )
