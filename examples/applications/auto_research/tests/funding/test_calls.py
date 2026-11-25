"""funding_calls with every source replaced by a fake and the roles scripted."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources as cite_sources
from auto_research.tasks.funding import calls as wf
from auto_research.tasks.funding import sources

TEX = r"""\documentclass{article}
\title{Learning-Rate Schedules for Linear Probes}
\begin{document}
\begin{abstract}
Linear probes on frozen features measure representation quality, but their accuracy depends on the
optimizer schedule. We tune short SGD learning rate schedules for linear probing of pretrained vision models.
\end{abstract}
\end{document}
"""

PROBE = ("Research on representation learning, linear probing of pretrained vision models and the "
         "optimizer learning rate schedule used to evaluate frozen features.")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "project.md").write_text("Kind: method\nClaim: A tuned SGD schedule lowers probe loss.\n")
    return repo


def call(source, cid, title, number, agency="NSF", status="open", deadlines=("2027-02-10",), text=""):
    return {"source": source, "id": cid, "number": number, "title": title, "funder": f"{agency} funder",
            "agency": agency, "status": status, "deadline": "", "deadlines": list(deadlines),
            "url": f"https://example.org/{cid}", "text": text, "budget": ""}


GRANTS = {
    "grants:1": call("grants", "grants:1", "Robust Intelligence: representation learning", "NSF-24-1"),
    "grants:2": call("grants", "grants:2", "Old linear probing call", "NSF-20-2", deadlines=("2025-01-01",)),
    "grants:3": call("grants", "grants:3", "Drug dose dumping", "FOR-FD-24-006", agency="HHS-FDA",
                     status="forecast", deadlines=()),
}
DETAILS = {
    "grants:1": PROBE + " The program supports research on optimizer schedules for linear probing.",
    "grants:2": PROBE,
    "grants:3": "Modified release oral drug products are at risk of alcohol dose dumping.",
    "grants:4": "Projects on machine learning for vision models and representation learning of images.",
}
NIH_CALL = call("grants", "grants:4", "Machine learning for imaging", "PA-25-169", agency="HHS-NIH11",
                deadlines=("2027-05-07",))
EU = [call("eu", "eu:HORIZON-1", "Trustworthy representation learning", "HORIZON-1", agency="HORIZON-CL4",
           deadlines=("2026-01-01", "2027-03-01"), text=PROBE + " Proposals should study linear probing."),
      call("eu", "eu:HORIZON-2", "Past AI topic", "HORIZON-2", agency="HORIZON-CL4", deadlines=("2024-03-19",),
           text=PROBE)]
NSF = [{"source": "nsf", "id": "nsf:9", "title": "Representation learning schedules", "program": "Robust Intelligence",
        "opportunity": "", "year": "2025", "amount": "500000", "url": "https://nsf/9", "text": PROBE}]
NIH = [{"source": "nih", "id": "nih:R01", "title": "Linear probing of vision models", "program": "NIBIB",
        "opportunity": "PA-25-169", "year": "2026", "amount": "1", "url": "https://nih/1", "text": PROBE}]


class Day(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 24)


def fake(monkeypatch, fail=()):
    asked = {"grants": [], "eu": [], "nsf": [], "nih": [], "detail": []}

    def grants_search(keyword="", number="", rows=25):
        asked["grants"].append(keyword or number)
        if "grants" in fail:
            cite_sources.UNAVAILABLE.add(sources.LABEL["grants"])
            return []
        if number:
            return [dict(NIH_CALL)] if number == "PA-25-169" else []
        return [dict(c) for c in GRANTS.values()]

    def grants_detail(cid):
        asked["detail"].append(cid)
        return {"text": DETAILS[cid], "deadline": "", "budget": "", "eligibility": "", "link": "", "title": "",
                "number": "", "funder": "", "additional": "", "posted": "", "archive": ""}

    def eu_search(text, size=50):
        asked["eu"].append(text)
        if "eu" in fail:
            cite_sources.UNAVAILABLE.add(sources.LABEL["eu"])
            return []
        return [dict(c) for c in EU]

    monkeypatch.setattr(sources, "grants_search", grants_search)
    monkeypatch.setattr(sources, "grants_detail", grants_detail)
    monkeypatch.setattr(sources, "eu_search", eu_search)
    monkeypatch.setattr(sources, "nsf_awards", lambda q, rows=25: asked["nsf"].append(q) or [dict(p) for p in NSF])
    monkeypatch.setattr(sources, "nih_projects", lambda q, years, rows=25: asked["nih"].append(q) or [dict(p) for p in NIH])
    monkeypatch.setattr(wf, "today_is", lambda: date(2026, 11, 24))
    return asked


KEYWORDS = {"topics": ["linear probing", "quantum chromodynamics"], "fields": ["learning rate schedules", "vision"]}


def verdicts():
    return {"calls": [
        {"id": "grants:1", "verdict": "fit", "why": "资助表示学习研究",
         "quote": "supports research on optimizer schedules for linear probing"},
        {"id": "grants:4", "verdict": "partial", "why": "偏向医学影像", "quote": "machine learning for vision models"},
        {"id": "grants:3", "verdict": "no", "why": "药物", "quote": "at risk of alcohol dose dumping"},
        {"id": "eu:HORIZON-1", "verdict": "fit", "why": "引文不对", "quote": "a sentence the call never wrote"},
    ]}


def run(paper, **kw):
    with script_roles({wf.keyworder: [KEYWORDS], wf.ranker: [verdicts()]}) as calls:
        out = json.loads(wf.funding_calls(str(paper), **kw))
    return out, calls


def test_open_calls_ranked_with_evidence(paper, monkeypatch):
    asked = fake(monkeypatch)
    out, calls = run(paper, count="10")
    assert asked["grants"][:2] == ['"linear probing"', '"learning rate schedules"']      # off-topic phrase dropped
    assert "PA-25-169" in asked["grants"]                                           # NIH opportunity followed
    assert "Learning-Rate Schedules for Linear Probes" in calls[wf.keyworder][0].text
    ranked = calls[wf.ranker][0].text
    assert "grants:1" in ranked and "grants:2" not in ranked and "HORIZON-2" not in ranked
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "funding_calls"
    rows = list(csv.DictReader((folder / "calls.csv").open(encoding="utf-8")))
    assert list(rows[0]) == ["title", "funder", "id", "deadline", "url", "fit", "why"]
    assert [r["id"] for r in rows] == ["NSF-24-1", "PA-25-169", "HORIZON-1"]          # fit, partial, unjudged
    assert rows[0]["deadline"] == "2027-02-10" and rows[2]["deadline"] == "2027-03-01"
    assert "Representation learning schedules" in rows[0]["why"]                      # NSF programme evidence
    assert "Linear probing of vision models" in rows[1]["why"]                         # NIH opportunity evidence
    assert "含检索词" in rows[2]["why"]                                                 # quote failed: code reason
    assert int(rows[0]["fit"]) > 0
    record = json.loads((folder / "calls.json").read_text())
    assert {c["id"] for c in record["dropped"]["past_deadline"]} == {"grants:2", "eu:HORIZON-2"}
    assert out["status"] == "PASS_WITH_NOTES" and out["calls"] == 3                     # fewer than 10
    report = (folder / "report.md").read_text()
    assert "基金项目检索" in report and "已资助的相似项目" in report and "call_requirements" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "keywords", "search", "filter", "rank", "choose", "report"]


def test_funder_narrows_sources_and_calls(paper, monkeypatch):
    asked = fake(monkeypatch)
    with script_roles({wf.keyworder: [KEYWORDS], wf.ranker: [{"calls": [
            {"id": "eu:HORIZON-1", "verdict": "fit", "why": "合适", "quote": "Proposals should study linear probing"}]}]}):
        out = json.loads(wf.funding_calls(str(paper), funder="eu", count="1"))
    assert not asked["grants"] and not asked["nsf"] and asked["eu"]
    assert out["calls"] == 1 and out["status"] == "PASS"
    asked = fake(monkeypatch)
    with script_roles({wf.keyworder: [KEYWORDS], wf.ranker: [{"calls": [
            {"id": "grants:1", "verdict": "fit", "why": "合适",
             "quote": "supports research on optimizer schedules for linear probing"}]}]}):
        out = json.loads(wf.funding_calls(str(paper), funder="NSF", count="5"))
    record = json.loads(Path(out["json"]).read_text())
    assert [c["id"] for c in record["calls"]] == ["grants:1"] and not asked["eu"] and not asked["nih"]


def test_a_failing_source_is_a_note_and_all_failing_is_fail(paper, monkeypatch):
    fake(monkeypatch, fail=("eu",))
    with script_roles({wf.keyworder: [KEYWORDS], wf.ranker: [verdicts()]}):
        out = json.loads(wf.funding_calls(str(paper), funder="eu"))
    assert out["status"] == "FAIL" and out["unavailable_sources"] == ["EU Funding & Tenders"]
    assert "没有响应" in Path(out["report"]).read_text()


def test_unknown_ids_and_inputs():
    shown = [{"id": "a", "title": "T", "text": "the call supports deep learning research"}]
    notes = wf.apply_verdicts(shown, [{"id": "zzz", "verdict": "fit", "why": "x", "quote": "deep learning research x"},
                                      {"id": "a", "verdict": "fit", "why": "ok", "quote": "supports deep learning research"}])
    assert "zzz" in notes[0] and shown[0]["verdict"] == "fit"
    with pytest.raises(ValueError, match="count"):
        wf.parse_count("0")
    assert wf.parse_count("") == 10
    with pytest.raises(ValueError, match="own words"):
        wf.check_keywords({"topics": ["quantum chromodynamics"], "fields": ["physics"]}, "linear probes")
    assert wf.parse_funder("eu") == (("eu",), "") and wf.parse_funder("NIH")[1] == "nih"


def test_dates_and_html():
    assert sources.day("02/10/2027") == "2027-02-10" and sources.day("2027-01-28T00:00:00.000+0000") == "2027-01-28"
    assert sources.day("1801094400000") == "2027-01-28" and sources.day("") == ""
    assert sources.plain_html("<p>A &amp; B</p><script>x()</script><li>C</li>") == "A & B\nC"
