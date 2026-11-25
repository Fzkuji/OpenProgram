"""call_requirements with the call read from fakes or local files and the extractor scripted."""

import json
from datetime import date
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources as cite_sources
from auto_research.tasks.funding import requirements as wf
from auto_research.tasks.funding import sources

CALL_HTML = """<html><head><style>p{}</style></head><body>
<h1>Robust Intelligence (RI)</h1>
<p>The Project Description is limited to 15 pages.</p>
<p>The Project Summary must not exceed 4,600 characters.</p>
<p>Every proposal must contain a section titled Broader Impacts.</p>
<p>Every proposal must include a Data Management Plan.</p>
<p>Proposals are due by 5 p.m. on February 10, 2027.</p>
<p>Budgets may not exceed $600,000 in total.</p>
<p>Only institutions of higher education may apply.</p>
</body></html>"""


def draft(paper: Path):
    folder = paper / "drafts" / "grant_proposal" / "20261101-000000-000000"
    folder.mkdir(parents=True)
    words = " ".join(["word"] * 8000)                    # 16 pages at 500 words a page
    grant = {"funder": "nsf", "funder_name": "NSF", "language": "en", "sections": [
        {"id": "summary", "title": "Project Summary", "paragraphs": [
            {"heading": "Overview", "kind": "context", "text": "We study probes.", "support": []}]},
        {"id": "description", "title": "Project Description", "paragraphs": [
            {"heading": "Intellectual Merit", "kind": "proposed", "text": words, "support": []},
            {"heading": "Broader Impacts", "kind": "proposed", "text": "Open tools.", "support": []}]}]}
    (folder / "grant.json").write_text(json.dumps(grant))


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (tmp_path / "call.html").write_text(CALL_HTML)
    return repo


def items():
    return {"items": [
        {"kind": "page_limit", "requirement": "Project Description at most 15 pages.",
         "quote": "The Project Description is limited to 15 pages.", "applies_to": "Project Description", "value": 15,
         "date": ""},
        {"kind": "char_limit", "requirement": "Summary at most 4600 characters.",
         "quote": "Project Summary must not exceed 4,600 characters", "applies_to": "Project Summary", "value": 4600,
         "date": ""},
        {"kind": "section", "requirement": "Broader Impacts section.", "quote": "a section titled Broader Impacts",
         "applies_to": "Broader Impacts", "value": 0, "date": ""},
        {"kind": "section", "requirement": "Data Management Plan.", "quote": "must include a Data Management Plan",
         "applies_to": "Data Management Plan", "value": 0, "date": ""},
        {"kind": "deadline", "requirement": "Due February 10, 2027.", "quote": "due by 5 p.m. on February 10, 2027",
         "applies_to": "", "value": 0, "date": "2027-02-10"},
        {"kind": "budget", "requirement": "At most $600,000.", "quote": "Budgets may not exceed $600,000 in total.",
         "applies_to": "", "value": 600000, "date": ""},
        {"kind": "eligibility", "requirement": "Universities only.", "quote": "Only institutions of higher education",
         "applies_to": "", "value": 0, "date": ""},
        {"kind": "page_limit", "requirement": "Plan at most 2 pages.", "quote": "must include a Data Management Plan",
         "applies_to": "Data Management Plan", "value": 2, "date": ""},
        {"kind": "format", "requirement": "Invented.", "quote": "use 12 point Times New Roman", "applies_to": "",
         "value": 0, "date": ""},
    ]}


def run(paper, call, reply=None):
    with script_roles({wf.extractor: [reply or items()]}) as calls:
        out = json.loads(wf.call_requirements(str(paper), call))
    return out, calls


def test_checklist_and_draft_check(paper, monkeypatch):
    monkeypatch.setattr(wf, "today_is", lambda: date(2026, 11, 24))
    draft(paper)
    out, calls = run(paper, str(paper.parent / "call.html"))
    assert "limited to 15 pages" in calls[wf.extractor][0].text and "p{}" not in calls[wf.extractor][0].text
    checklist = json.loads((paper / "workflow" / "call_requirements.json").read_text())
    assert [i["id"] for i in checklist["items"]] == [f"R{n}" for n in range(1, 9)]       # invented quote dropped
    record = json.loads(Path(out["json"]).read_text())
    assert [d["requirement"] for d in record["dropped"]] == ["Invented."]
    checks = {c["id"]: c for c in record["checks"]}
    assert checks["R1"]["result"] == "over" and "16" in checks["R1"]["detail"]          # 8000 words, 15 pages
    assert checks["R2"]["result"] == "ok" and checks["R2"]["section"] == "Project Summary"
    assert checks["R3"]["result"] == "ok" and checks["R4"]["result"] == "missing"
    assert checks["R5"]["result"] == "ok" and "78" in checks["R5"]["detail"]
    author = {a["id"]: a for a in record["author"]}
    assert set(author) == {"R6", "R7", "R8"} and author["R8"]["why"] == "引文里没有这个数值"
    assert out["status"] == "FAIL" and out["failed"] == 2
    report = Path(out["report"]).read_text()
    assert "草稿核对" in report and "需要你自己确认" in report and "超出" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["read", "extract", "verify", "check", "report"]


def test_without_a_draft_everything_is_listed(paper, monkeypatch):
    monkeypatch.setattr(wf, "today_is", lambda: date(2027, 3, 1))
    out, _ = run(paper, str(paper.parent / "call.html"))
    record = json.loads(Path(out["json"]).read_text())
    assert [c["result"] for c in record["checks"]] == ["passed"]                          # the deadline is gone
    assert out["status"] == "FAIL" and "还没有申请书草稿" in Path(out["report"]).read_text()
    assert {a["why"] for a in record["author"]} >= {"没有草稿可核对", "需要你确认"}


def test_grants_url_reads_the_api_and_the_linked_solicitation(paper, monkeypatch):
    monkeypatch.setattr(wf, "today_is", lambda: date(2026, 11, 24))
    monkeypatch.setattr(sources, "grants_detail", lambda gid: {
        "text": "Supports research on learning.", "deadline": "2027-02-10", "budget": "600000",
        "eligibility": "Public and private universities", "link": "https://nsf.example/program", "title": "RI",
        "number": "NSF-24-1", "funder": "NSF", "additional": "", "posted": "", "archive": ""})
    pages = {"https://nsf.example/program": b'<p>Program page</p><a href="/pubs/sol.htm">View Solicitation</a>',
             "https://nsf.example/pubs/sol.htm": CALL_HTML.encode()}
    fetched = []
    monkeypatch.setattr(sources, "fetch_page", lambda url: fetched.append(url) or pages.get(url))
    out, calls = run(paper, "https://www.grants.gov/search-results-detail/320753")
    assert fetched == ["https://nsf.example/program", "https://nsf.example/pubs/sol.htm"]
    text = calls[wf.extractor][0].text
    assert "Award ceiling: 600000" in text and "Program page" in text and "limited to 15 pages" in text
    assert out["items"] == 8


def test_eu_url_and_unreadable_call(paper, monkeypatch):
    monkeypatch.setattr(sources, "eu_topic", lambda eid: {
        "title": "QML", "identifier": eid, "call": "C", "deadlines": ["2027-01-28"],
        "description": "Expected Outcome: quantum learning.", "conditions": "The page limit of the application is 70 pages.",
        "support": ""})
    reply = {"items": [{"kind": "page_limit", "requirement": "70 pages.", "quote": "page limit of the application is 70 pages",
                        "applies_to": "", "value": 70, "date": ""}]}
    out, calls = run(paper, "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
                            "topic-details/HORIZON-JU-EUROHPC-2026-QML-07-01", reply)
    assert "Deadline dates: 2027-01-28" in calls[wf.extractor][0].text and out["items"] == 1
    monkeypatch.setattr(sources, "fetch_page", lambda url: cite_sources.UNAVAILABLE.add("x.org") or None)
    out = json.loads(wf.call_requirements(str(paper), "https://x.org/call"))
    assert out["status"] == "FAIL" and out["items"] == 0
    with pytest.raises(ValueError, match="not a URL"):
        wf.call_requirements(str(paper), "/no/such/file.pdf")
    with pytest.raises(ValueError, match="empty"):
        wf.call_requirements(str(paper), " ")


def test_pdf_call(paper):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(paper.parent / "call.pdf", "wb") as f:
        writer.write(f)
    read = wf.read_call(str(paper.parent / "call.pdf"), paper / "out", [])
    assert read["kind"] == "pdf" and not (paper / "out" / "call.pdf").exists()


def test_url_ids():
    assert wf.grants_id("https://www.grants.gov/search-results-detail/320753") == "320753"
    assert wf.grants_id("https://example.org/320753") == ""
    assert wf.eu_id("https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
                    "topic-details/HORIZON-CL4-2027-01-02?x=1") == "HORIZON-CL4-2027-01-02"


def test_a_limit_takes_the_unit_its_quote_names():
    assert wf.unit_kind("word_limit", "no more than five pages per proposal") == "page_limit"
    assert wf.unit_kind("page_limit", "at most 4,600 characters") == "char_limit"
    assert wf.unit_kind("page_limit", "15 pages or 7,500 words") == "page_limit"
    assert wf.unit_kind("budget", "five pages") == "budget"
