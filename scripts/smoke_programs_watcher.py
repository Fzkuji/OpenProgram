"""Smoke test: the runtime auto-detect loop for installed programs.

Proves the backend half of "drop a harness in → it's usable":
  * fingerprint changes when a harness dir appears
  * rescan() imports it and the registry gains its @agentic_function
  * a second rescan reports nothing new (idempotent)

Runs offline in a temp dir; never touches the real agentics/ or state.
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


def _make_harness(agentics: Path, repo: str, pkg: str, fn: str) -> None:
    p = agentics / repo / pkg / "agentics"
    p.mkdir(parents=True)
    (agentics / repo / pkg / "__init__.py").write_text("", encoding="utf-8")
    (p / "__init__.py").write_text(textwrap.dedent(f'''
        from openprogram.agentic_programming.function import agentic_function
        @agentic_function(name="{fn}")
        def {fn}(x: str = "") -> str:
            "auto-detected harness fn"
            return x
        AGENTIC_FUNCTIONS = [{fn}]
    ''').lstrip(), encoding="utf-8")


def main() -> int:
    from openprogram.functions import watcher as W
    from openprogram.functions._registry import rescan
    from openprogram.functions._runtime import get as get_tool

    base = Path(tempfile.mkdtemp(prefix="op_watch_"))
    agentics = base / "agentics"
    agentics.mkdir()
    try:
        # baseline fingerprint (empty-ish dir)
        fp0 = W._fingerprint(str(agentics))

        # nothing installed yet → fn absent
        check("target fn absent before install", get_tool("watched_fn") is None)

        # "install" a harness as a real directory
        _make_harness(agentics, "Watched-Harness", "watched_pkg", "watched_fn")

        # fingerprint must change (this is what wakes the watcher)
        fp1 = W._fingerprint(str(agentics))
        check("fingerprint changes when a harness dir appears", fp0 != fp1,
              f"{len(fp0)}→{len(fp1)} entries")

        # rescan picks it up → registry gains the function
        result = rescan(str(agentics))
        check("rescan reports the new fn as added",
              "watched_fn" in result.get("added", []), str(result.get("added")))
        check("new @agentic_function is live in the registry",
              get_tool("watched_fn") is not None, "watched_fn registered")

        # second rescan is idempotent — nothing new
        result2 = rescan(str(agentics))
        check("second rescan adds nothing (idempotent)",
              result2.get("added") == [], str(result2.get("added")))
    finally:
        for p in list(sys.path):
            if str(base) in p:
                sys.path.remove(p)
        for m in [m for m in sys.modules if m.startswith("watched_pkg")]:
            del sys.modules[m]
        shutil.rmtree(base, ignore_errors=True)
        print("# cleaned up")

    print()
    print(f"=== {len(failures)} FAIL: {failures} ===" if failures else "=== ALL PASS ===")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
