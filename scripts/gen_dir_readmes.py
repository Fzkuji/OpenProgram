"""Generate ``openprogram/<dir>/README.md`` from each ``__init__.py`` docstring.

Per-folder READMEs make the repo navigable in any file browser / GitHub
without opening Python files. The ``__init__.py`` module docstrings
describe each package; this script lifts them into Markdown so the
description shows up at folder level too.

The READMEs are derived data — re-run this script after editing any
``openprogram/<dir>/__init__.py`` docstring to refresh them. The
``__init__.py`` docstring stays the canonical source of truth so
Python introspection (``help()``, doc tools) and the README don't
drift.

Usage:

    python scripts/gen_dir_readmes.py

What it does per directory:

* Skips if a README.md already exists (idempotent — won't trample a
  hand-written one). Two such hand-written READMEs currently live
  under ``openprogram/context/`` and ``openprogram/memory/``.
* Extracts the module docstring from ``__init__.py``.
* Lists direct-child ``.py`` files with one-line summaries pulled
  from their own module docstrings.
* Lists sub-packages with the same treatment.
* Writes ``README.md`` with a footer noting the auto-gen origin.

Run from the repo root.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path("openprogram")


def _module_docstring(init_path: Path) -> str | None:
    """Parse the file's module-level docstring without executing it."""
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    return ast.get_docstring(tree)


def _list_python_files(dir_path: Path) -> list[Path]:
    """Direct-child .py files, excluding __init__.py and tests."""
    out = []
    for p in sorted(dir_path.glob("*.py")):
        if p.name in ("__init__.py",) or "_test" in p.name or p.name.endswith(".test.py"):
            continue
        out.append(p)
    return out


def _list_subdirs(dir_path: Path) -> list[Path]:
    out = []
    for p in sorted(dir_path.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("__"):
            continue
        out.append(p)
    return out


def _one_line_summary(py_path: Path) -> str:
    """First sentence of the file's module docstring."""
    doc = _module_docstring(py_path) or ""
    first = next((l for l in doc.splitlines() if l.strip()), "")
    for sep in (". ", " — ", " - "):
        if sep in first:
            first = first.split(sep, 1)[0]
            break
    return first.strip(" .")


def _readme_for(dir_path: Path) -> str:
    """Build the README body."""
    name = dir_path.name
    doc = _module_docstring(dir_path / "__init__.py") or ""

    lines = [l.rstrip() for l in doc.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    title_line = lines[0] if lines else f"openprogram/{name}"
    body_lines = lines[1:]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    py_files = _list_python_files(dir_path)
    subdirs = _list_subdirs(dir_path)

    out: list[str] = []
    out.append(f"# `openprogram/{name}/`")
    out.append("")
    out.append(f"> {title_line}")
    out.append("")
    if body_lines:
        out.append("## Overview")
        out.append("")
        out.extend(body_lines)
        out.append("")

    if py_files:
        out.append("## Files in this directory")
        out.append("")
        for p in py_files:
            summary = _one_line_summary(p)
            out.append(f"- **`{p.name}`** — {summary}" if summary else f"- **`{p.name}`**")
        out.append("")

    if subdirs:
        out.append("## Sub-packages")
        out.append("")
        for d in subdirs:
            sub_init = d / "__init__.py"
            summary = _one_line_summary(sub_init) if sub_init.exists() else ""
            out.append(f"- **`{d.name}/`** — {summary}" if summary else f"- **`{d.name}/`**")
        out.append("")

    out.append(
        "_Auto-generated from `__init__.py` docstring — keep that as the "
        "source of truth; re-run `python scripts/gen_dir_readmes.py` "
        "from the repo root to refresh._"
    )
    out.append("")
    return "\n".join(out)


def main() -> int:
    if not ROOT.exists():
        print(f"error: run from the repo root — no {ROOT}/ here", file=sys.stderr)
        return 2

    written = 0
    refreshed = 0
    skipped_handwritten = 0
    skipped_nodoc = 0
    AUTOGEN_MARKER = "Auto-generated from `__init__.py`"

    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        readme = d / "README.md"
        if readme.exists():
            existing = readme.read_text(encoding="utf-8", errors="replace")
            if AUTOGEN_MARKER not in existing:
                # Hand-written README — leave alone.
                skipped_handwritten += 1
                continue
            # Auto-generated previously — refresh in place.
            new_body = _readme_for(d)
            if new_body != existing:
                readme.write_text(new_body, encoding="utf-8")
                refreshed += 1
                print(f"  refreshed {readme}")
            continue
        # No README yet.
        body = _readme_for(d)
        if "## Overview" not in body and "## Files in this directory" not in body:
            skipped_nodoc += 1
            continue
        readme.write_text(body, encoding="utf-8")
        written += 1
        print(f"  wrote {readme}")
    print(
        f"\n{written} new README(s), {refreshed} refreshed, "
        f"{skipped_handwritten} hand-written left alone, "
        f"{skipped_nodoc} skipped (no content)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
