"""Smoke test: a third-party harness installed as a REAL directory under
agentics/ (i.e. ``git clone``-d, NOT symlinked) is auto-discovered and
its @agentic_function registers.

This is the behavior the install flow promises: drop a harness folder
into agentics/, it just works — no symlink (which needs admin/developer
mode on Windows). Runs fully offline in a temp dir; touches no real state.
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import textwrap
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def _make_fake_harness(agentics_dir: Path, repo_name: str, pkg: str, fn: str) -> None:
    """Create a real-directory harness:
        <agentics>/<repo_name>/<pkg>/__init__.py
        <agentics>/<repo_name>/<pkg>/agentics/__init__.py  (exports AGENTIC_FUNCTIONS)
    """
    pkg_dir = agentics_dir / repo_name / pkg
    (pkg_dir / "agentics").mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "agentics" / "__init__.py").write_text(textwrap.dedent(f'''
        """Fake third-party harness for the smoke test."""
        from openprogram.agentic_programming.function import agentic_function

        @agentic_function(name="{fn}")
        def {fn}(x: str = "") -> str:
            "A fake third-party agentic function."
            return f"ran {fn} with {{x}}"

        AGENTIC_FUNCTIONS = [{fn}]
    ''').lstrip(), encoding="utf-8")


def main() -> int:
    # A temp agentics/ dir we fully control — NOT the real one.
    base = Path(tempfile.mkdtemp(prefix="op_3p_harness_"))
    try:
        agentics = base / "agentics"
        agentics.mkdir()

        # 1. a third-party harness as a REAL directory (hyphenated, like a clone)
        _make_fake_harness(agentics, "Cool-Third-Party-Harness", "cool_harness", "cool_fn")
        # 2. an internal-style single-module dir (no inner agentics/ pkg) —
        #    must NOT be misdetected as a harness.
        (agentics / "just_a_module").mkdir()
        (agentics / "just_a_module" / "__init__.py").write_text("", encoding="utf-8")

        from openprogram.functions import _registry as R

        # discovery should yield the real-dir harness…
        found = dict(R._iter_external_harness_dirs(str(agentics)))
        check("real-directory harness is discovered (no symlink needed)",
              "Cool-Third-Party-Harness" in found,
              str(sorted(found)))
        check("plain single-module dir is NOT yielded as a harness... or is filtered later",
              True,  # informational; final filter is _find_python_package
              "just_a_module present" if "just_a_module" in found else "skipped at iter")

        # the package finder resolves the inner pkg for the harness, and
        # returns None for the plain module dir.
        pkg = R._find_python_package(str(agentics / "Cool-Third-Party-Harness"))
        check("inner package located for the harness",
              pkg is not None and pkg.endswith("cool_harness"), str(pkg))
        none_pkg = R._find_python_package(str(agentics / "just_a_module"))
        check("plain module dir → no harness package (won't be imported)",
              none_pkg is None, str(none_pkg))

        # actually import it → the @agentic_function fires + registers.
        R._import_external_harness(str(agentics / "Cool-Third-Party-Harness"))
        from openprogram.functions._runtime import get as get_tool
        tool = get_tool("cool_fn")
        check("third-party @agentic_function registered after import",
              tool is not None, "cool_fn in registry" if tool else "MISSING")

        # official first-party programs are skipped by discovery (loaded
        # by _programs instead) — verify their clone-dir names are excluded.
        skip = R._official_program_dir_names()
        check("official harness dir-names are in the skip set",
              {"GUI-Agent-Harness", "Research-Agent-Harness"} <= skip,
              str(sorted(skip)))
    finally:
        # drop the temp pkg from sys.path + modules so reruns are clean
        for p in list(sys.path):
            if str(base) in p:
                sys.path.remove(p)
        for m in [m for m in sys.modules if m.startswith("cool_harness")]:
            del sys.modules[m]
        shutil.rmtree(base, ignore_errors=True)
        print(f"# cleaned up {base}")

    print()
    print(f"=== {len(failures)} FAIL: {failures} ===" if failures else "=== ALL PASS ===")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
