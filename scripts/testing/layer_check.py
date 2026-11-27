"""The layer contracts, for the commit being made.

``python -m scripts.testing.layer_check --staged`` checks the staged Python
files under ``openprogram/`` against ``tests/contracts/repository/test_layer_order.py``
(no import of a higher layer) and ``test_layer_internals.py`` (no cycle between
the parts of a layer). Staged files are read from the index, so another
session's unsaved edit cannot fail this commit for an upward import; a cycle is
judged on the working tree and reported only when one of its imports is in a
staged file. Exit 1 lists what to fix; exit 0 otherwise.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "contracts" / "repository"))

import test_layer_internals as internals  # noqa: E402
import test_layer_order as layers  # noqa: E402


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def staged() -> list[str]:
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "openprogram")
    return [n for n in names.splitlines() if n.endswith(".py")]


def upward(files: list[str]) -> list[str]:
    modules = {layers._module_name(p) for p in layers.PACKAGE.rglob("*.py") if "__pycache__" not in p.parts}
    found = []
    for relative in files:
        if relative in layers.COMPOSITION_ROOTS:
            continue
        path = ROOT / relative
        own = layers._layer(layers._module_name(path))
        if own is None:
            continue
        for name, line in sorted(layers._imported_modules(path, _git("show", f":{relative}"))):
            target = layers._layer(name) if name in modules else None
            if target is not None and target > own:
                found.append(f"{relative}:{line}: {name}  [{layers.LAYERS[own]} -> {layers.LAYERS[target]}]")
    return found


def cycles(files: list[str]) -> list[str]:
    chosen = set(files)
    _, assembly = internals.graph()
    lines = internals.cycle_imports() + assembly
    return [line for line in lines if line.split(":", 1)[0] in chosen]


def main(argv: list[str]) -> int:
    files = staged() if "--staged" in argv else [a for a in argv if a.endswith(".py")]
    if not files:
        return 0
    up, round_ = upward(files), cycles(files)
    for line in up:
        print(f"  {line}", file=sys.stderr)
    for line in round_:
        print(f"  {line}", file=sys.stderr)
    if up or round_:
        print(
            "commit refused: these imports go up a layer or close a cycle inside one; "
            "move the code to where it belongs or hand it down through a port "
            "(tests/contracts/repository/test_layer_order.py and test_layer_internals.py explain)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
