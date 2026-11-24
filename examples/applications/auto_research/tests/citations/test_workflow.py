"""The entry with the sources replaced and scripted roles."""

import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import lookup as lookup_module
from auto_research.tasks.citations import sources
from auto_research.tasks.citations import workflow as wf

REAL = {"source": "openalex", "title": "Agent Workflow Memory", "authors": ["Zora Wang", "Graham Neubig"],
        "year": 2024, "venue": "ICML", "doi": "", "url": "u1"}
OTHER = {"source": "crossref", "title": "Workflow memory for web agents", "authors": ["Q Li"],
         "year": 2023, "venue": "J", "doi": "", "url": "u2"}
BIB = """@article{real, title={Agent Workflow Memory}, author={Wang, Zora}, journal={ICML}, year={2024}}
@article{fake, title={Recursive gradient folding for sparse lattices}, author={Zhang, L.}, journal={JMLR}, year={2021}}
"""


@pytest.fixture
def offline(monkeypatch):
    def search(query):
        return [dict(REAL)] if "agent workflow memory" in query.lower() else [dict(OTHER)]
    for name in ("openalex", "semantic_scholar", "crossref", "arxiv", "openreview", "openlibrary"):
        monkeypatch.setattr(sources, name, search)
    monkeypatch.setattr(sources, "arxiv_id", lambda identifier: [dict(REAL, source="arxiv", venue="arXiv")])
    monkeypatch.setattr(sources, "crossref_doi", lambda doi: [])
    monkeypatch.setattr(sources, "FIRST", (search,))
    monkeypatch.setattr(sources, "WIDER", (search,))
    monkeypatch.setattr(sources, "SEARCHES", {"openreview": search, "crossref": search})
    monkeypatch.setattr(lookup_module, "WORKERS", 1)


def run(tmp_path, **kw):
    bib = tmp_path / "refs.bib"
    bib.write_text(BIB)
    return json.loads(wf.citation_check(str(bib), **kw))


def test_off_makes_no_model_call(offline, tmp_path):
    with script_roles({wf.investigator: [], wf.judge: []}) as calls:
        out = run(tmp_path, investigate="off")
    assert out["counts"]["authentic"] == 1 and out["counts"]["inauthentic"] == 1
    assert out["status"] == "FAIL" and out["flagged"] == ["fake"]
    assert not calls.get(wf.investigator) and not calls.get(wf.judge)
    assert "未找到" in open(out["report"], encoding="utf-8").read()


def test_judge_sees_candidates_and_cannot_invent(offline, tmp_path):
    with script_roles({wf.investigator: [{"queries": [{"id": 1, "queries": ["gradient folding lattices"]}]}],
                       wf.judge: [{"verdicts": [{"id": 1, "choice": "none", "assessment": "inauthentic",
                                                 "note": "没有同名文献"}]}]}) as calls:
        out = run(tmp_path)
    request = calls[wf.judge][0]
    assert "a record on the same topic is not a match" in request.text      # docstring
    assert "Workflow memory for web agents" in request.text                  # candidates
    assert "Agent Workflow Memory" not in request.text                       # settled refs stay out
    assert out["counts"]["inauthentic"] == 1
    report = json.load(open(out["report"].replace(".md", ".json"), encoding="utf-8"))
    assert report[1]["investigated"] and report[1]["note"] == "没有同名文献"
    record = json.load(open(out["run"], encoding="utf-8"))
    assert set(record["roles"]) == {"reader", "investigator", "judge"} and record["roles"]["judge"]["effort"] == "high"
    assert [s["stage"] for s in record["stages"]] == ["read", "lookup", "plan queries", "search", "judge", "report"]
    judge_stage = record["stages"][4]
    assert judge_stage["role"] == "judge" and judge_stage["schema"] == "reference_verdicts"
    assert "Workflow memory for web agents" in json.dumps(judge_stage["context"], ensure_ascii=False)
    assert judge_stage["output"]["verdicts"][0]["choice"] == "none"
    assert "a record on the same topic is not a match" in judge_stage["instructions"]


def test_unknown_candidate_is_rejected():
    with pytest.raises(ValueError):
        wf.check_verdicts({"verdicts": [{"id": 0, "choice": "c9", "assessment": "authentic", "note": ""}]},
                          [0], {0: [REAL]})


def test_reader_splits_free_text(offline, tmp_path):
    reply = {"references": [{"text": "[1] Wang, Z. (2024). Agent Workflow Memory. ICML. arXiv:2409.07429",
                             "title": "Agent Workflow Memory", "authors": ["Wang, Z."], "year": "2024",
                             "venue": "ICML", "doi": "", "arxiv": ""}]}
    with script_roles({wf.reader: [reply]}):
        out = json.loads(wf.citation_check("[1] Wang, Z. (2024). Agent Workflow Memory. ICML. arXiv:2409.07429\n",
                                           out_dir=str(tmp_path), investigate="off"))
    assert out["status"] == "PASS"
