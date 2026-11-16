"""``openprogram doctor`` — print the installation health checks.

The checks themselves are :mod:`openprogram.operations.diagnostics.checks`;
this command prints them as a plain-text table (or JSON) and exits non-zero
when any blocking check fails.
"""
from __future__ import annotations

import json

from openprogram.operations.diagnostics.checks import run_checks


def _cmd_doctor_credentials(repair: bool = False, as_json: bool = False) -> int:
    """Report that credential filesystem checks are disabled."""
    del repair
    if as_json:
        print(json.dumps({"enabled": False, "findings": []}, indent=2))
    else:
        print("Credential filesystem checks are disabled.")
    return 0


def _cmd_doctor(as_json: bool = False) -> int:
    results = run_checks()
    if as_json:
        print(json.dumps(results, indent=2))
        return 0 if all(r["ok"] for r in results) else 1
    width = max(len(r["label"]) for r in results) + 2
    fail_count = 0
    for r in results:
        mark = "INFO" if r.get("optional") and r.get("status") != "granted" else ("OK  " if r["ok"] else "FAIL")
        label = r["label"].ljust(width)
        print(f"  [{mark}] {label}{r['detail']}")
        if not r["ok"]:
            fail_count += 1
    print()
    if fail_count == 0:
        print("All checks passed.")
        return 0
    print(f"{fail_count} check(s) failed.")
    return 1
