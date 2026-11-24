"""Checklist rows and verdict."""

import importlib

report = importlib.import_module("auto_research.tasks.revision.steps.report")
checklist = importlib.import_module("auto_research.tasks.revision.rules.checklist")


def test_rows_take_the_worst_level_of_their_checks():
    findings = [{"level": "WARN", "check": "caption", "message": ""},
                {"level": "FAIL", "check": "placement", "message": ""},
                {"level": "REVIEW", "check": "rule 4", "message": ""}]
    rows = report.checklist(findings, "changes")
    assert len(rows) == len(checklist.CHECKLIST)
    by_item = {r["requirement"]: r["status"] for r in rows}
    assert by_item["Figures and tables at the top of the page"] == "FAIL"
    assert by_item["Captions in sentence case ending with a period"] == "WARN"
    assert by_item["Overall performance: phenomenon, reason, conclusion for each point"] == "REVIEW"
    assert by_item["Back up the LaTeX sources"] == "PASS"
    assert report.verdict(findings) == "FAIL" and report.verdict([]) == "PASS"
