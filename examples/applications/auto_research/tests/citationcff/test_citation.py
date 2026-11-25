"""software_citation with ORCID, DataCite, Zenodo, doi.org, arXiv and GitHub faked and the describer scripted."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citationcff import gather as g
from auto_research.tasks.citationcff import workflow as wf
from auto_research.tasks.citations import sources

TITLE = "Sparse Memory for Language Agents"
TEX = r"""\documentclass{article}
\title{Sparse Memory for Language Agents}
\author{Ada Lovelace \\ University of Analytical Engines \\ \texttt{ada@uae.edu} \and Bob van Dyke \\ Babbage Lab
\and Cy Twombly \\ Rome Institute}
\begin{document}
\maketitle
\begin{abstract}
We prune agent memory by usage. Accuracy stays at 73.5\% on 1{,}000 tasks while memory shrinks by 40\%. The method
needs no retraining. It works with any retriever.
\end{abstract}
\section{Method}
Memory pruning drops rarely read entries.
\end{document}
"""
CONCEPT = "10.5281/zenodo.1000"
VERSION_DOI = "10.5281/zenodo.1002"
REPO = "https://github.com/acme/probe"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    git(tmp_path, "init", "-q", str(repo))
    (repo / "main.tex").write_text(TEX)
    (repo / "refs.bib").write_text("@article{a, title={Retrieval Agents at Scale}, author={X, Y}}\n")
    (repo / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge, to any person")
    (repo / "train.py").write_text("print(1)\n")
    (repo / "workflow" / "project.md").write_text("Kind: method\nPublished: arXiv:2401.00001\n")
    (repo / "workflow" / "datasheet.md").write_text("License: CC-BY-4.0\nLocation: https://huggingface.co/datasets/acme/mem\n")
    git(repo, "-c", "user.name=Ada Lovelace", "-c", "user.email=ada@uae.edu", "add", ".")
    git(repo, "-c", "user.name=Ada Lovelace", "-c", "user.email=ada@uae.edu", "commit", "-qm", "init")
    git(repo, "tag", "v1.2.0")
    git(repo, "remote", "add", "origin", "git@github.com:acme/probe.git")
    return repo


def fake(monkeypatch, zenodo=True, orcid_down=False, own_title=TITLE):
    seen = {"orcid": [], "works": []}

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        if url.endswith("/expanded-search/"):
            if orcid_down:
                sources.UNAVAILABLE.add("ORCID")
                return None
            q = params["q"]
            seen["orcid"].append(q)
            if '"Ada"' in q:
                return {"expanded-result": [
                    {"orcid-id": "0000-0001-0000-0001", "institution-name": ["University of Analytical Engines"]},
                    {"orcid-id": "0000-0001-0000-0002", "institution-name": ["Other College"]}]}
            if '"Bob"' in q:
                return {"expanded-result": [{"orcid-id": "0000-0002-0000-0001", "institution-name": []},
                                            {"orcid-id": "0000-0002-0000-0002", "institution-name": []}]}
            return {"expanded-result": [{"orcid-id": "0000-0003-0000-0001", "institution-name": ["Somewhere"]}]}
        if url.endswith("/works"):
            seen["works"].append(url)
            title = TITLE if "0000-0002-0000-0002" in url else "Unrelated"
            return {"group": [{"work-summary": [{"title": {"title": {"value": title}}}]}]}
        if url == g.ZENODO:
            if not zenodo:
                return {"hits": {"hits": []}}
            return {"hits": {"hits": [
                {"doi": VERSION_DOI, "conceptdoi": CONCEPT, "metadata": {
                    "title": "acme/probe: v1.2.0", "version": "v1.2.0", "publication_date": "2025-03-01",
                    "related_identifiers": [{"identifier": "https://github.com/acme/probe/tree/v1.2.0"}]}},
                {"doi": "10.5281/zenodo.9", "conceptdoi": "10.5281/zenodo.8", "metadata": {
                    "title": "other/probe", "related_identifiers": [{"identifier": "https://github.com/other/probe"}]}}]}}
        if url == g.DATACITE:
            return {"data": []}
        if url.startswith(g.HANDLE):
            return {"responseCode": 1} if CONCEPT in url else None
        raise AssertionError(url)

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(sources, "arxiv_id", lambda i: [{"title": own_title, "year": 2024}])
    monkeypatch.setattr(g, "github", lambda url, path="": {"full_name": url.split("github.com/")[1]})
    g._canonical.clear()
    return seen


DESC = {"abstract": "We prune agent memory by usage. Accuracy stays at 73.5% on 1,000 tasks.",
        "keywords": ["agent memory", "memory pruning", "quantum chemistry"]}


def run(paper, desc=DESC, **kw):
    with script_roles({wf.describer: [desc]}) as calls:
        out = json.loads(wf.software_citation(str(paper), **kw))
    return out, calls


def test_cited_doi_passes(paper, monkeypatch):
    seen = fake(monkeypatch)
    (paper / "refs.bib").write_text((paper / "refs.bib").read_text() + f"@software{{c, doi={{{CONCEPT}}}}}\n")
    out, calls = run(paper)
    assert out["status"] == "PASS", out
    assert out["doi"] == CONCEPT and out["doi_resolves"] is True and out["cited_in"] == "refs.bib"
    assert "at most three" in calls[wf.describer][0].text and "73.5" in calls[wf.describer][0].text
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "software_citation"
    cff = yaml.safe_load((folder / "CITATION.cff").read_text())
    assert cff["cff-version"] == "1.2.0" and cff["title"] == TITLE and cff["version"] == "1.2.0"
    assert cff["doi"] == CONCEPT and cff["license"] == "MIT" and cff["repository-code"] == REPO
    ada, bob, cy = cff["authors"]
    assert ada == {"family-names": "Lovelace", "given-names": "Ada", "affiliation": "University of Analytical Engines",
                   "orcid": "https://orcid.org/0000-0001-0000-0001"}
    assert bob["family-names"] == "van Dyke" and bob["orcid"].endswith("0000-0002-0000-0002")   # by its works
    assert "orcid" not in cy                                                                     # unique but unmatched
    assert cff["keywords"] == ["agent memory", "memory pruning"]                                 # invented one dropped
    assert cff["abstract"] == DESC["abstract"]
    ref = cff["preferred-citation"]
    assert ref["doi"] == "10.48550/arXiv.2401.00001" and ref["year"] == 2024 and ref["type"] == "article"
    assert {"type": "doi", "value": VERSION_DOI, "description": "Archived version v1.2.0"} in cff["identifiers"]
    assert {"type": "url", "value": "https://huggingface.co/datasets/acme/mem", "description": "Dataset"} in cff["identifiers"]
    zen = json.loads((folder / ".zenodo.json").read_text())
    assert zen["upload_type"] == "software" and zen["license"] == "mit" and zen["creators"][0]["name"] == "Lovelace, Ada"
    meta = json.loads((folder / "codemeta.json").read_text())
    assert meta["identifier"] == f"https://doi.org/{CONCEPT}" and meta["programmingLanguage"] == ["Python"]
    assert meta["license"] == "https://spdx.org/licenses/MIT"
    assert not wf.validate_cff(cff)
    report = (folder / "report.md").read_text()
    assert "Cy Twombly" in report and "cp '" in report


def test_no_archive_gives_zenodo_steps(paper, monkeypatch):
    fake(monkeypatch, zenodo=False)
    out, _ = run(paper)
    assert out["status"] == "PASS_WITH_NOTES" and out["doi"] == ""
    report = (Path(out["report"]).parent / "report.md").read_text()
    assert "Account → GitHub" in report and "@software{lovelace" in report and "10.5281/zenodo.XXXXXXX" in report
    cff = yaml.safe_load((Path(out["report"]).parent / "CITATION.cff").read_text())
    assert "doi" not in cff


def test_found_but_not_cited(paper, monkeypatch):
    fake(monkeypatch)
    out, _ = run(paper)
    assert out["status"] == "PASS_WITH_NOTES" and out["doi"] == CONCEPT and out["cited_in"] == ""
    assert f"doi       = {{{CONCEPT}}}" in (Path(out["report"]).parent / "report.md").read_text()


def test_invented_abstract_falls_back(paper, monkeypatch):
    fake(monkeypatch)
    bad = {"abstract": "Built at Google, it reaches 99.9% accuracy.", "keywords": ["agent memory", "x", "y"]}
    out, _ = run(paper, desc=bad)
    rec = json.loads((Path(out["report"]).parent / "citation.json").read_text())
    assert any("99.9" in d and "Google" in d for d in rec["dropped"])
    assert rec["facts"]["abstract_written"].startswith("We prune agent memory by usage.")
    assert len(wf.split_sentences(rec["facts"]["abstract_written"])) == 3


def test_no_authors_fails(paper, monkeypatch):
    fake(monkeypatch)
    (paper / "main.tex").write_text(TEX.replace(TEX[TEX.index(r"\author"):TEX.index(r"\begin{document}")], ""))
    out, _ = run(paper)
    assert out["status"] == "FAIL" and out["problems"] >= 1
    assert "authors is missing" in (Path(out["report"]).parent / "report.md").read_text()


def test_source_down_is_a_note(paper, monkeypatch):
    fake(monkeypatch, orcid_down=True)
    out, _ = run(paper)
    assert out["status"] == "PASS_WITH_NOTES" and out["orcids"] == 0
    assert "ORCID" in (Path(out["report"]).parent / "report.md").read_text()


def test_wrong_own_record_left_out(paper, monkeypatch):
    fake(monkeypatch, own_title="BERT: Pre-training of Deep Bidirectional Transformers")
    out, _ = run(paper)
    rec = json.loads((Path(out["report"]).parent / "citation.json").read_text())
    assert rec["facts"]["paper"] == {} and any("not of this paper" in n for n in rec["notes"])
    cff = yaml.safe_load((Path(out["report"]).parent / "CITATION.cff").read_text())
    assert "doi" not in cff["preferred-citation"]


def test_code_dir_from_rules(paper, tmp_path, monkeypatch):
    fake(monkeypatch, zenodo=False)
    code = tmp_path / "code"
    code.mkdir()
    git(tmp_path, "init", "-q", str(code))
    git(code, "remote", "add", "origin", "https://github.com/acme/other.git")
    (code / "LICENSE").write_text("                Apache License\n           Version 2.0, January 2004\n")
    (paper / "workflow" / "rules.json").write_text(json.dumps({"code": {"repo": str(code)}}))
    out, _ = run(paper)
    rec = json.loads((Path(out["report"]).parent / "citation.json").read_text())
    assert rec["facts"]["code_from"] == "rules.json code.repo" and rec["facts"]["license"] == "Apache-2.0"
    assert rec["facts"]["repository"] == "https://github.com/acme/other" and rec["facts"]["version"] == "0.1.0"
    with pytest.raises(ValueError):
        wf.software_citation(str(paper), code_dir=str(tmp_path / "missing"))


def test_validator_flags_bad_values():
    cff = {"cff-version": "1.2.0", "message": "m", "title": "t", "date-released": "2025-13-01",
           "authors": [{"family-names": "A", "orcid": "0000-0001"}], "doi": "zenodo", "colour": "red"}
    problems = " ".join(wf.validate_cff(cff))
    assert "date-released" in problems and "orcid" in problems and "doi zenodo" in problems and "colour" in problems
    assert yaml.safe_load(wf.to_yaml({"a": "x: \"y\"", "b": [{"c": 1, "d": "é"}]})) == {"a": "x: \"y\"",
                                                                                       "b": [{"c": 1, "d": "é"}]}


def test_keyword_case_follows_paper():
    assert wf.paper_case("frozen imagenet features", "Probing Frozen ImageNet features works.") == "frozen ImageNet features"


def test_split_name_and_affiliations():
    assert g.split_name("Ludwig van Beethoven") == {"given-names": "Ludwig", "family-names": "van Beethoven"}
    assert g.split_name("Mary Ann Smith") == {"given-names": "Mary Ann", "family-names": "Smith"}
    places = g.affiliations(TEX)
    assert places["Ada Lovelace"] == ["University of Analytical Engines"] and places["Bob van Dyke"] == ["Babbage Lab"]
