"""Every package imports only the layers below it: the design matrix is lower triangular.

``openprogram/`` is nine layers, bottom to top:

1. ``host._platform`` — filesystem, locking, process and terminal bindings;
2. ``state`` — stores, memory, settings;
3. ``host`` — the machine: backends, sandbox, processes, security;
4. ``models`` — providers, auth, usage;
5. ``runtime`` — the Agent, conversations, jobs, permissions;
6. ``extensions`` — plugins, skills, the MCP client;
7. ``operations`` — the worker, self-update, diagnostics;
8. ``programs`` — the tools and applications the Agent calls;
9. ``interfaces`` — the Server, CLI, ACP, channels, speech.

A module imports its own layer and the layers below it, never one above.
When a lower layer needs something only an upper one has, the upper layer
hands it down through a port the lower layer owns and the upper layer fills
when it loads (``runtime.lower_layers``, ``programs._host.lower_layers``,
``runtime.program_host``). Read with the layers as both the functional
requirements and the design parameters of the system, this keeps the design
matrix lower triangular: changing a layer can only affect the layers above
it, so each can be changed in that order without going round in circles.

Only the composition roots import upward, because assembling the layers is
what they are for. There are no other exceptions, at module level or inside
a function: the scan reads every module as source, so it also sees imports
inside functions, relative imports, and string imports through
``importlib.import_module`` and ``__import__``.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "openprogram"

# Bottom to top. The longest matching prefix decides a module's layer.
LAYERS = (
    "openprogram.host._platform",
    "openprogram.state",
    "openprogram.host",
    "openprogram.models",
    "openprogram.runtime",
    "openprogram.extensions",
    "openprogram.operations",
    "openprogram.programs",
    "openprogram.interfaces",
)

# They assemble the layers, so they may import any of them.
COMPOSITION_ROOTS = frozenset({
    # Importing ``openprogram`` installs how the Programs layer loads.
    "openprogram/__init__.py",
    # ``python -m openprogram`` runs the CLI application.
    "openprogram/__main__.py",
    # The worker process starts the Server, the channels and the Programs.
    "openprogram/operations/worker/runner.py",
})


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _layer(name: str) -> int | None:
    matches = [index for index, prefix in enumerate(LAYERS) if _within(name, prefix)]
    if not matches:
        return None
    return max(matches, key=lambda index: len(LAYERS[index]))


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(path: Path, source: str | None = None) -> set[tuple[str, int]]:
    """Every module ``path`` imports, resolved to absolute names, with its line;
    read from ``source`` when given (the staged text, for the commit hook)."""
    text = path.read_text(encoding="utf-8") if source is None else source
    tree = ast.parse(text, filename=str(path))
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            # ``from openprogram.programs import tools`` imports a module.
            names = [source, *(f"{source}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            first = node.args[0]
            if callee in {"import_module", "__import__"} and isinstance(first, ast.Constant):
                if isinstance(first.value, str):
                    names = [first.value]
        found.update((name, node.lineno) for name in names)
    return found


def upward_imports() -> list[str]:
    """``file:line: imported`` for every import of a higher layer."""
    paths = [p for p in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]
    modules = {_module_name(p) for p in paths}
    found: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if relative in COMPOSITION_ROOTS:
            continue
        own = _layer(_module_name(path))
        if own is None:
            continue
        for name, line in sorted(_imported_modules(path)):
            if name not in modules:
                continue  # ``package.attribute`` guesses
            target = _layer(name)
            if target is not None and target > own:
                found.append(f"{relative}:{line}: {name}  [{LAYERS[own]} -> {LAYERS[target]}]")
    return sorted(set(found))


def test_every_layer_imports_only_the_layers_below_it():
    found = upward_imports()
    assert found == [], (
        "a module imports a layer above its own; have the upper layer hand it down "
        "through a port the lower layer owns, or move the code to the layer it "
        "belongs to (do not add an exception):\n" + "\n".join(f"  {line}" for line in found)
    )


def test_the_composition_roots_exist():
    missing = sorted(root for root in COMPOSITION_ROOTS if not (ROOT / root).is_file())
    assert missing == []


if __name__ == "__main__":  # ``python tests/contracts/repository/test_layer_order.py [prefix]``
    import sys

    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    lines = [line for line in upward_imports() if line.startswith(prefix)]
    print("\n".join(lines))
    print(f"{len(lines)} upward imports", file=sys.stderr)
