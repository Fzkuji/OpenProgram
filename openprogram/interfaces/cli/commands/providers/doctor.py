"""``openprogram providers doctor`` — print the credential health report."""
from __future__ import annotations
import json

from openprogram.models.auth.doctor import run_doctor


def _cmd_doctor(as_json: bool) -> int:
    """Run the full credential health report.

    Thin wrapper over :func:`openprogram.models.auth.doctor.run_doctor`,
    which the REST route also calls.
    """
    report = run_doctor()
    findings = report["findings"]

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor_report(
            report["pools_checked"],
            report["accounts_checked"],
            findings,
        )

    has_error = any(f["level"] == "ERROR" for f in findings)
    return 1 if has_error else 0



def _print_doctor_report(pools_count: int, accounts_count: int, findings) -> None:
    print(f"Checked {pools_count} pool(s) across {accounts_count} account(s).\n")
    if not findings:
        print("✓ All checks passed.")
        return
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings_sorted = sorted(findings, key=lambda f: (order.get(f["level"], 3), f["code"]))
    tag = {"ERROR": "✗ ERROR", "WARN": "⚠ WARN ", "INFO": "· INFO "}
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for f in findings_sorted:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
        print(f"{tag.get(f['level'], f['level']):8s}  [{f['code']}] {f['message']}")
    print()
    print(f"Summary: {counts.get('ERROR', 0)} error(s), "
          f"{counts.get('WARN', 0)} warning(s), "
          f"{counts.get('INFO', 0)} info.")

