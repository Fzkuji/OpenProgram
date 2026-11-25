"""tech_transfer with the assessor and patent_reader scripted and PatentsView faked with
the documented PatentSearch response shape ({error, count, total_hits, patents})."""

import json
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.impact import patents as pt
from auto_research.tasks.impact import transfer as tt

TEX = r"""\documentclass{article}
\title{Short Schedules for Linear Probes}
\begin{document}
\begin{abstract}
A short SGD schedule lowers the validation loss of linear probes on frozen features.
\end{abstract}
\end{document}
"""

ABSTRACT_A = ("A method trains a linear probe on frozen neural network features with a learning rate schedule "
              "selected by validation loss.")
ABSTRACT_B = "A wind turbine blade with a serrated trailing edge reduces noise."


@pytest.fixture
def paper(tmp_path):
    p = tmp_path / "paper"
    (p / "workflow").mkdir(parents=True)
    (p / "main.tex").write_text(TEX)
    (p / "workflow" / "claims.md").write_text(
        "# Claims\nApproved: yes\n\nC1: A tuned short SGD schedule lowers linear probe validation loss from 0.0137 "
        "to 0.0102.\nRQ: RQ1\n")
    (p / "workflow" / "evidence.csv").write_text(
        "id,claim,statement,source,script\nE1,C1,Validation loss 0.0102 with the tuned schedule against 0.0137 "
        "with the default,workflow/results.tsv,train.py\n")
    (p / "workflow" / "results.tsv").write_text(
        "commit\tmetric\tstatus\tseconds\tdescription\n5dfd31a\t0.013727\tkeep\t3\tbaseline lr 0.01 5 epochs\n"
        "d68462a\t0.010215\tkeep\t3\tlr 0.05, 10 epochs\n")
    return p


def assessment(level=4, evidence=None, phrases=("linear probe schedule", "validation loss probe")):
    return {"level": level, "reason": "在合成数据上验证了核心功能。",
            "evidence": evidence or [
                {"row": "evidence:E1", "quote": "Validation loss 0.0102 with the tuned schedule", "shows": "实验结果"},
                {"row": "results:d68462a", "quote": "a sentence that is not in the row", "shows": "x"}],
            "next_level": ["在真实视觉特征上验证"], "phrases": list(phrases),
            "applications": [{"area": "模型评估工具", "reason": "降低探针评估成本"},
                             {"area": "风电", "reason": "无关领域"}]}


def response(patents):
    return {"error": False, "count": len(patents), "total_hits": len(patents), "patents": patents}


PATENTS = [
    {"patent_id": "11000001", "patent_title": "Training linear probes with learned schedules",
     "patent_abstract": ABSTRACT_A, "patent_date": "2023-05-02",
     "assignees": [{"assignee_organization": "Acme Vision Inc."}, {"assignee_organization": "Acme Vision Inc."}],
     "cpc_current": [{"cpc_group_id": "G06N3/08"}, {"cpc_group_id": "G06N20/00"}]},
    {"patent_id": "11000002", "patent_title": "Serrated blade", "patent_abstract": ABSTRACT_B,
     "patent_date": "2022-01-04", "assignees": [{"assignee_organization": "Windco"}],
     "cpc_current": [{"cpc_group_id": "F03D1/06"}]},
]


def overlap():
    return {"pairs": [
        {"patent": "11000001", "claim": "C1", "overlap": "high",
         "quote": "a learning rate schedule selected by validation loss", "reason": "同一机制。"},
        {"patent": "11000002", "claim": "C1", "overlap": "low", "quote": "a quote not in the abstract at all",
         "reason": "领域不同。"}],
        "areas": [{"patent": "11000001", "areas": ["模型评估工具"]}, {"patent": "11000002", "areas": []}]}


@pytest.fixture
def keys(monkeypatch):
    """No key set; a test sets the ones it needs."""
    for k in ("USPTO_API_KEY", "PATENTSVIEW_API_KEY", "USPTO_ODP_URL", "PATENTSVIEW_URL"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def fake_requests(monkeypatch, handler):
    sent = []

    def request(url, body, headers, as_json=True):
        sent.append((url, body, headers))
        return handler(url, body)

    monkeypatch.setattr(pt, "request", request)
    return sent


# The ODP patent application search response (swagger PatentDataResponse).
ODP_ITEMS = [
    {"applicationNumberText": "17000001", "applicationMetaData": {
        "inventionTitle": "Linear probe training with a validation loss learning rate schedule",
        "patentNumber": "11000001", "grantDate": "2023-05-02", "filingDate": "2021-01-04",
        "cpcClassificationBag": ["G06N3/08", "G06N20/00"], "firstApplicantName": "Acme Vision Inc.",
        "applicantBag": [{"applicantNameText": "Acme Vision Inc."}]},
     "assignmentBag": [{"assigneeBag": [{"assigneeNameText": "ACME VISION INC"}]}]},
    {"applicationNumberText": "17000002", "applicationMetaData": {
        "inventionTitle": "Wind turbine blade", "filingDate": "2022-03-01",
        "earliestPublicationNumber": "US 2023-0012345 A1", "earliestPublicationDate": "2023-01-12",
        "cpcClassificationBag": ["F03D1/06"], "applicantBag": [{"applicantNameText": "Windco"}]}},
]
GRANT_XML = ("<?xml version='1.0'?><!DOCTYPE us-patent-grant SYSTEM 'us-patent-grant-v47.dtd' []>"
             "<us-patent-grant><abstract><p>" + ABSTRACT_A + "</p></abstract><claims><claim><claim-text>1. A method "
             "of training a linear probe.</claim-text></claim></claims></us-patent-grant>")


def odp_handler(xml=True):
    def handler(url, body):
        if url == pt.ODP:
            return 200, {"count": 2, "patentFileWrapperDataBag": ODP_ITEMS}
        if url == pt.ODP_DOCS.format("17000001"):
            uri = "https://api.uspto.gov/api/v1/download/applications/17000001/grant.xml" if xml else \
                "https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/2023/ipg230502.zip"
            return 200, {"count": 1, "patentFileWrapperDataBag": [{"applicationNumberText": "17000001",
                                                                    "grantDocumentMetaData": {"fileLocationURI": uri}}]}
        if url.endswith("grant.xml"):
            return 200, GRANT_XML
        return 404, None
    return handler


def test_without_a_key_the_patent_source_is_not_configured(paper, keys):
    fake_requests(keys, lambda url, body: pytest.fail("no request without a key"))
    with script_roles({tt.assessor: [assessment()]}) as calls:
        out = json.loads(tt.tech_transfer(str(paper)))
    ctx = calls[tt.assessor][0].text
    assert "Component and/or breadboard validation in laboratory environment" in ctx and "results:d68462a" in ctx
    assert out["trl"] == 4 and not out["capped"] and out["patent_source"] == "not configured"
    assert out["status"] == "PASS_WITH_NOTES" and out["patents"] == 0
    record = json.loads(Path(out["json"]).read_text())
    assert [e["row"] for e in record["trl"]["evidence"]] == ["evidence:E1"]
    assert len(record["trl"]["dropped"]) == 1
    report = Path(out["report"]).read_text()
    assert "专利来源未配置" in report and "USPTO_API_KEY" in report and "没有专利数据，不列公司" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "assess", "check", "report"]


def test_odp_first_with_abstract_from_the_grant_xml(paper, keys):
    keys.setenv("USPTO_API_KEY", "odp-key")
    keys.setenv("PATENTSVIEW_API_KEY", "pv-key")
    sent = fake_requests(keys, odp_handler())
    reply = overlap()
    reply["pairs"][1]["patent"] = "US20230012345A1"
    reply["areas"][1]["patent"] = "US20230012345A1"
    with script_roles({tt.assessor: [assessment()], tt.patent_reader: [reply]}) as calls:
        out = json.loads(tt.tech_transfer(str(paper)))
    url, body, headers = sent[0]
    assert url == pt.ODP and headers == {"X-API-KEY": "odp-key"}
    assert body["q"] == "applicationMetaData.inventionTitle:(linear AND probe AND schedule)"
    assert body["pagination"] == {"offset": 0, "limit": pt.PER_PHRASE}
    assert body["sort"] == [{"field": "applicationMetaData.filingDate", "order": "desc"}]
    assert not any(u == pt.PATENTSVIEW for u, _, _ in sent)                     # ODP answered: no second source
    assert out["patent_source"] == "USPTO ODP" and out["patents"] == 2
    record = json.loads(Path(out["json"]).read_text())
    by = {p["number"]: p for p in record["patents"]}
    assert by["11000001"]["abstract"] == ABSTRACT_A and by["11000001"]["assignees"] == ["ACME VISION INC"]
    assert by["11000001"]["url"].endswith("US11000001") and "1. A method of training" in by["11000001"]["claims"]
    assert by["US20230012345A1"]["kind"] == "application" and by["US20230012345A1"]["assignees"] == ["Windco"]
    assert ABSTRACT_A in calls[tt.patent_reader][0].text
    pairs = {o["patent"]: o for o in record["overlap"]}
    assert pairs["11000001"]["overlap"] == "high" and pairs["US20230012345A1"]["overlap"] == "unverified"
    areas = {a["area"]: a for a in record["areas"]}
    assert areas["模型评估工具"]["companies"] == ["ACME VISION INC"]
    report = Path(out["report"]).read_text()
    assert "来源 USPTO ODP" in report and "（申请）" in report and "只依据标题" not in report


def test_odp_titles_only_when_no_single_xml(paper, keys):
    keys.setenv("USPTO_API_KEY", "odp-key")
    sent = fake_requests(keys, odp_handler(xml=False))
    reply = {"pairs": [{"patent": "11000001", "claim": "C1", "overlap": "high",
                        "quote": "validation loss learning rate schedule", "reason": "标题相同机制。"}],
             "areas": []}
    with script_roles({tt.assessor: [assessment(phrases=("linear probe schedule", "linear probe schedule"))],
                       tt.patent_reader: [reply]}):
        out = json.loads(tt.tech_transfer(str(paper)))
    assert not any(u.endswith(".zip") for u, _, _ in sent)                    # a bulk zip is never downloaded
    record = json.loads(Path(out["json"]).read_text())
    assert record["source"]["titles_only"]
    assert {o["patent"]: o for o in record["overlap"]}["11000001"]["overlap"] == "medium"   # title only: at most medium
    assert "重合判断只依据标题" in Path(out["report"]).read_text()


def test_odp_refused_falls_back_to_patentsview(paper, keys):
    keys.setenv("USPTO_API_KEY", "bad")
    keys.setenv("PATENTSVIEW_API_KEY", "pv-key")

    def handler(url, body):
        return (403, None) if url == pt.ODP else (200, response(PATENTS))

    sent = fake_requests(keys, handler)
    with script_roles({tt.assessor: [assessment()], tt.patent_reader: [overlap()]}):
        out = json.loads(tt.tech_transfer(str(paper)))
    pv = [(b, h) for u, b, h in sent if u == pt.PATENTSVIEW]
    body, headers = pv[0]
    assert headers == {"X-Api-Key": "pv-key"} and len(pv) == 2
    assert body["q"]["_or"][0] == {"patent_abstract": {"_text_all": "linear probe schedule"}}
    assert body["o"] == {"size": pt.PER_PHRASE} and "assignees.assignee_organization" in body["f"]
    assert out["patent_source"] == "PatentsView" and out["patents"] == 2 and "USPTO ODP" in out["unavailable_sources"]
    record = json.loads(Path(out["json"]).read_text())
    assert record["patents"][0]["number"] == "11000001"                       # closest to the claim first
    assert record["patents"][0]["assignees"] == ["Acme Vision Inc."] and record["patents"][0]["cpc"][0] == "G06N3/08"
    pairs = {o["patent"]: o for o in record["overlap"]}
    assert pairs["11000001"]["overlap"] == "high" and pairs["11000002"]["overlap"] == "unverified"
    areas = {a["area"]: a for a in record["areas"]}
    assert areas["模型评估工具"]["companies"] == ["Acme Vision Inc."] and areas["风电"]["companies"] == []
    report = Path(out["report"]).read_text()
    assert "USPTO ODP 拒绝了 API key（HTTP 403）" in report and "Acme Vision Inc." in report
    assert "Windco" not in report.split("## 可能的应用领域")[1]


def test_odp_no_match_is_an_empty_answer(paper, keys):
    keys.setenv("USPTO_API_KEY", "k")
    fake_requests(keys, lambda url, body: (404, None))
    with script_roles({tt.assessor: [assessment()]}):
        out = json.loads(tt.tech_transfer(str(paper)))
    assert out["patent_source"] == "USPTO ODP" and out["patents"] == 0 and not out["unavailable_sources"]
    assert "没有找到专利" in Path(out["report"]).read_text()


def test_level_is_capped_without_lab_evidence_and_phrases_use_project_words(paper, keys):
    reply = assessment(level=6, evidence=[{"row": "claim:C1", "quote": "A tuned short SGD schedule", "shows": "主张"}],
                       phrases=("quantum annealing hardware", "zebra telemetry"))
    with script_roles({tt.assessor: [reply]}):
        out = json.loads(tt.tech_transfer(str(paper)))
    record = json.loads(Path(out["json"]).read_text())
    assert out["trl"] == tt.LAB and out["capped"] and record["trl"]["proposed"] == 6
    assert record["trl"]["phrases"] and all("quantum" not in p for p in record["trl"]["phrases"])
    assert "检索词用了项目里没有的词" in Path(out["report"]).read_text()


def test_a_silent_patentsview_is_a_note(paper, keys):
    keys.setenv("PATENTSVIEW_API_KEY", "k")
    fake_requests(keys, lambda url, body: (0, None))
    with script_roles({tt.assessor: [assessment()]}):
        out = json.loads(tt.tech_transfer(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES" and out["patents"] == 0 and out["patent_source"] == "unavailable"
    assert "PatentsView 没有响应" in Path(out["report"]).read_text()


def test_unknown_patent_in_reply_raises():
    patents = [pt.patentsview_record(p) for p in PATENTS]
    bad = {"pairs": [{"patent": "999", "claim": "C1", "overlap": "high", "quote": "", "reason": ""}], "areas": []}
    with pytest.raises(ValueError, match="unknown patent"):
        tt.check_overlap(bad, patents, {"C1": "x"}, [], [])


def test_xml_texts_and_publication_ids():
    abstract, claims = pt.xml_texts(GRANT_XML)
    assert abstract == ABSTRACT_A and claims.startswith("1. A method")
    assert pt.xml_texts("not xml") == ("", "")
    assert pt.publication_id("US 2014-0167116 A1") == "US20140167116A1"


def test_no_claims_raises(tmp_path, keys):
    p = tmp_path / "p"
    (p / "workflow").mkdir(parents=True)
    with pytest.raises(ValueError, match="no claims"):
        tt.tech_transfer(str(p))
