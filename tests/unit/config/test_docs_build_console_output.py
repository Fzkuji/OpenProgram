"""The docs build prints only ASCII.

The worker rebuilds the docs site in the background on a source checkout,
with its output going to a log whose encoding follows the system locale.
A Windows locale that cannot encode a character such as "→" made the last
status line raise after every page was rendered, so the new site was never
swapped in and /docs stayed empty.
"""
from __future__ import annotations

import ast
from pathlib import Path

DOCS_SITE = Path(__file__).resolve().parents[3] / "scripts" / "docs_site"


def _printed_strings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
            for arg in node.args:
                for part in ast.walk(arg):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        yield node.lineno, part.value


def test_docs_build_status_lines_are_ascii():
    offenders = []
    for path in sorted(DOCS_SITE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, text in _printed_strings(tree):
            if not text.isascii():
                offenders.append(f"{path.name}:{line}: {text!r}")
    assert offenders == []
