"""citation_graph with OpenAlex and Crossref replaced by a fake fetch."""

import json
import urllib.parse

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.library import citation_graph
from auto_research.tasks.survey import search

BIB = r"""@article{alpha, title={Alpha Memory for Agents}, author={Li, A.}, doi={10.1/a}, year={2023}}
@article{beta, title={Beta Planning}, author={Wu, B.}, eprint={2401.00001}, year={2024}}
@inproceedings{gamma, title={Gamma Study of Memory}, author={Xu, C.}, year={2022}}
@article{delta, title={Delta Not Anywhere}, author={Yu, D.}, doi={10.1/d}, year={2020}}
@article{epsilon, title={Epsilon Benchmark}, author={Zu, E.}, doi={10.1/w12}, year={2021}}
"""


def work(n, title, refs=(), cited=10, doi="", retracted=False):
    return {"id": f"https://openalex.org/W{n}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
            "publication_year": 2020, "publication_date": "2020-01-01", "cited_by_count": cited,
            "is_retracted": retracted, "referenced_works": [f"https://openalex.org/W{r}" for r in refs],
            "primary_location": {"source": {"display_name": "Venue"}}, "locations": [], "authorships": []}


def offline(monkeypatch, works_by_doi, title_hits, crossref):
    pool = {w["id"].rsplit("/", 1)[1]: w for w in list(works_by_doi.values()) + list(title_hits.values()) + OTHERS}
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        calls.append(url)
        if url.startswith("https://api.openalex.org/works/doi:"):
            return works_by_doi.get(url.split("doi:", 1)[1])
        if url == "https://api.openalex.org/works" and "search" in params:
            hit = title_hits.get(params["search"])
            return {"results": [hit] if hit else []}
        if url == "https://api.openalex.org/works" and params.get("filter", "").startswith("openalex_id:"):
            ids = params["filter"].split(":", 1)[1].split("|")
            return {"results": [pool[i] for i in ids if i in pool]}
        if url.startswith("https://api.crossref.org/works/"):
            doi = urllib.parse.unquote(url.rsplit("/works/", 1)[1])
            return {"message": crossref.get(doi, {})}
        raise AssertionError(url)

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "fetch", fetch)
    return calls


OTHERS = [work(10, "Foundational Memory Networks", cited=9000), work(11, "Eleven Retrieval", cited=50),
          work(12, "Epsilon Benchmark", doi="10.1/w12", cited=800), work(13, "Thirteen Only Once", cited=5),
          work(14, "Fourteen Attention", cited=500)]


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    (repo / "references.bib").write_text(BIB)
    (repo / "drafts").mkdir()
    (repo / "drafts" / "old.bib").write_text("@article{ignored, title={Should Not Be Read}}")
    return repo


def test_retractions_corrections_and_missing_works(paper, monkeypatch):
    by_doi = {"10.1/a": work(1, "Alpha Memory for Agents", [10, 11, 12, 14, 2], doi="10.1/a"),
              "10.48550/arxiv.2401.00001": work(2, "Beta Planning", [10, 11])}
    hits = {"Gamma Study of Memory": work(3, "Gamma Study of Memory", [10, 12, 13, 14], retracted=True)}
    crossref = {"10.1/a": {"updated-by": [{"type": "correction", "DOI": "10.1/a-corr"}]}}
    offline(monkeypatch, by_doi, hits, crossref)
    out = json.loads(citation_graph(str(paper), min_shared="2"))

    assert out["status"] == "FAIL"
    kinds = {(p["key"], p["kind"]): p for p in out["problems"]}
    assert set(kinds) == {("alpha", "correction"), ("gamma", "retraction")}
    assert kinds[("alpha", "correction")]["notice_url"] == "https://doi.org/10.1/a-corr"
    assert kinds[("gamma", "retraction")]["notice_url"] == "https://openalex.org/W3"
    # W10 by three entries; W14 and W11 by two each, W14 more cited; W12 is epsilon by DOI; W13 by one
    assert [(m["openalex"], m["shared"]) for m in out["missing"]] == [("W10", 3), ("W14", 2), ("W11", 2)]
    assert out["missing"][0]["cited_by"] == ["alpha", "beta", "gamma"]
    assert out["missing"][0]["title"] == "Foundational Memory Networks" and out["missing"][0]["year"] == 2020
    assert sorted(out["unresolved"]) == ["delta", "epsilon"]
    assert out["resolved"] == 3

    graph = json.load(open(out["graph"], encoding="utf-8"))
    assert {n["id"] for n in graph["nodes"]} == {"W1", "W2", "W3", "W10", "W14", "W11"}
    assert {"source": "W1", "target": "W2"} in graph["edges"]
    html = open(out["html"], encoding="utf-8").read()
    assert "<svg" in html and "<script" not in html and "Foundational Memory Networks" in html
    report = open(out["report"], encoding="utf-8").read()
    assert "撤稿" in report and "Foundational Memory Networks" in report and "delta" in report
    data = json.load(open(out["json"], encoding="utf-8"))
    assert data["resolved"]["beta"]["how"] == "arxiv" and data["resolved"]["gamma"]["how"] == "title"
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["read", "resolve", "notices", "missing", "report"]
    assert out["report"].startswith(str(paper / "drafts" / "citation_graph"))
    assert "Should Not Be Read" not in json.dumps(data)


def test_a_crossref_retraction_notice_fails_and_a_clean_list_passes(paper, monkeypatch):
    (paper / "references.bib").write_text("@article{alpha, title={Alpha Memory for Agents}, doi={10.1/a}}\n")
    by_doi = {"10.1/a": work(1, "Alpha Memory for Agents", [10], doi="10.1/a")}
    offline(monkeypatch, by_doi, {}, {"10.1/a": {"updated-by": [{"type": "retraction", "DOI": "10.1/a-ret"}]}})
    out = json.loads(citation_graph(str(paper), out_dir=str(paper / "out")))
    assert out["status"] == "FAIL"
    assert out["problems"][0]["notice_doi"] == "10.1/a-ret"

    offline(monkeypatch, by_doi, {}, {})
    out = json.loads(citation_graph(str(paper), out_dir=str(paper / "out")))
    assert out["status"] == "PASS" and out["missing"] == [] and out["problems"] == []


def test_bad_arguments(paper, tmp_path):
    with pytest.raises(ValueError):
        citation_graph(str(paper), min_shared="0")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        citation_graph(str(empty))
