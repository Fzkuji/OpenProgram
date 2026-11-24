"""Step 4: one checklist row per guide item, the verdict, and the saved report."""

import json
from pathlib import Path

from ...rules.checklist import CHECKLIST, PHASES

PHASE_NAMES = [name for name, _ in PHASES]

SEVERITY = {"FAIL": 3, "WARN": 2, "REVIEW": 1}


def checklist(findings: list[dict], scope: str, selected=None) -> list[dict]:
    """Every chosen item with how it is checked and its status: PASS, FAIL, WARN, REVIEW, TODO or N/A."""
    rows = []
    for phase, source, number, item, how, detail in (CHECKLIST if selected is None else selected):
        if how == "auto":
            hits = [f for f in findings if f["check"] in detail]
            status = max((f["level"] for f in hits), key=SEVERITY.get, default="PASS")
            note = f"{len(hits)} finding(s)" if hits else ""
        elif how == "review":
            hits = [f for f in findings if f["check"] in {f"rule {n}" for n in detail}]
            status = "REVIEW" if hits else "PASS"
            note = f"{len(hits)} finding(s)" if hits else f"reviewer read the {scope}"
        else:
            status, note = {"manual": "TODO", "built-in": "PASS", "n/a": "N/A"}[how], detail
        rows.append({"phase": phase, "source": source, "item": number, "requirement": item, "method": how,
                     "status": status, "note": note})
    return rows


def verdict(findings: list[dict]) -> str:
    levels = {f["level"] for f in findings}
    if "FAIL" in levels:
        return "FAIL"
    return "PASS_WITH_NOTES" if levels else "PASS"


def counts(findings: list[dict]) -> dict:
    result = {}
    for f in findings:
        result[f["level"]] = result.get(f["level"], 0) + 1
    return result


def save(out: Path, findings: list[dict], added: list[str], baseline: str, draft: Path, scope: str,
         trail=None, selected=None, sections=()) -> str:
    """Write report.json, report.md and the run record; return the short JSON result of the run."""
    status = verdict(findings)
    rows = checklist(findings, scope, selected)
    phases = list(dict.fromkeys(r["phase"] for r in rows))
    report = {"status": status, "baseline": baseline, "draft": str(draft), "scope": scope,
              "phases": phases, "sections": list(sections),
              "checklist": rows, "findings": findings, "added_paragraphs": added,
              "draft_pdf": str(out / "draft.pdf") if (out / "draft.pdf").exists() else None,
              "diff_pdf": str(out / "diff.pdf") if (out / "diff.pdf").exists() else None,
              "run": str(out / "run.json") if trail is not None else None}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (out / "report.md").write_text(markdown(report))
    if trail is not None:
        trail.step("report", status=status, todo=[r["requirement"] for r in rows if r["status"] == "TODO"])
        trail.save(out, report=out / "report.md", json=out / "report.json", diff_pdf=report["diff_pdf"] or "")
    return json.dumps({"status": status, "report": str(out / "report.md"), "diff_pdf": report["diff_pdf"],
                       "run": report["run"], "counts": counts(findings),
                       "manual_items": [r["requirement"] for r in rows if r["status"] == "TODO"]},
                      ensure_ascii=False)


def markdown(report: dict) -> str:
    lines = [f"# Paper revision check: {report['status']}", "",
             f"- Baseline: `{report['baseline']}`", f"- Draft: `{report['draft']}`",
             f"- Comparison PDF: `{report['diff_pdf']}`",
             f"- Phases: {'all' if len(report.get('phases', [])) == len(PHASE_NAMES) else ', '.join(report.get('phases', []))}",
             f"- Sections: {', '.join(report.get('sections') or []) or 'all'}", "",
             "## Checklist", "", "Phases in checking order; a problem in an early phase makes the later ones moot.", ""]
    phase = None
    for r in report["checklist"]:
        if r["phase"] != phase:
            phase = r["phase"]
            lines += ["", f"### {phase}", "", "| Source | Item | Requirement | Method | Status | Note |",
                      "| --- | --- | --- | --- | --- | --- |"]
        lines.append(f"| {r['source']} | {r['item']} | {r['requirement']} | {r['method']} | {r['status']} | {r['note']} |")
    lines.append("")
    for level in ("FAIL", "WARN", "REVIEW"):
        items = [f for f in report["findings"] if f["level"] == level]
        if items:
            lines += [f"## {level} ({len(items)})", ""]
            lines += [f"- **{f['check']}**: {f['message']}" for f in items]
            lines.append("")
    if not report["findings"]:
        lines.append("No findings.")
    return "\n".join(lines) + "\n"
