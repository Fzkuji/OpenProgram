"""facet_ideas with the abstracts and searches replaced and the roles scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.facets import abstracts as ab
from auto_research.tasks.facets import workflow as wf
from auto_research.tasks.ideas import workflow as ideas_wf
from auto_research.tasks.survey import search

TEX = r"""\documentclass{article}
\title{Warm Restarts for Linear Probes}
\begin{document}
\begin{abstract}
We tune the learning-rate schedule of linear probes on frozen features. Warm restarts cut the validation loss by 26\% on a synthetic regression benchmark.
\end{abstract}
\section{Intro} Probes \cite{resnet,bert}.
\end{document}
"""

BIB = """@inproceedings{bert, title={BERT: Pre-training of Deep Bidirectional Transformers}, year={2019}, eprint={1810.04805}}
@inproceedings{resnet, title={Deep Residual Learning for Image Recognition}, year={2016}, doi={10.1109/cvpr.2016.90}}
@misc{lost, title={A Work Without Abstract}, year={2020}}
"""

ABSTRACTS = {
    "resnet": ("We present a residual learning framework to ease the training of networks that are substantially "
               "deeper. We evaluate residual nets on the ImageNet dataset with a depth of up to 152 layers.", "OpenAlex"),
    "bert": ("We introduce BERT, which pre-trains deep bidirectional representations from unlabeled text. It obtains "
             "new state-of-the-art results on eleven natural language processing tasks.", "Semantic Scholar"),
}

PRIOR = search.candidate("Residual linear probes", "Residual connections in probes.", "2024-03-01", ["A B"], "ICLR",
                         arxiv="2403.00001")


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "refs.bib").write_text(BIB)
    monkeypatch.setattr(wf, "abstracts", lambda entries: {k: v for k, v in ABSTRACTS.items()
                                                         if k in {e["key"] for e in entries}})

    def fake(q, n, since):
        return [dict(PRIOR)] if "residual" in q else []
    monkeypatch.setattr(search, "SEARCHES", {"arxiv": fake})
    return repo


def facet(text, quote):
    return {"text": text, "quote": quote}


EMPTY = facet("", "")
FACETS = {"works": [
    {"id": "PROJECT", "purpose": facet("tune the optimizer schedule of linear probes",
                                       "tune the learning-rate schedule of linear probes"),
     "mechanism": facet("warm restarts of the learning rate", "Warm restarts cut the validation loss"),
     "evaluation": facet("validation loss on a synthetic regression benchmark", "on a synthetic regression benchmark")},
    {"id": "resnet", "purpose": facet("ease the training of much deeper networks", "ease the training of networks"),
     "mechanism": facet("residual learning framework", "We present a residual learning framework"),
     "evaluation": facet("ImageNet with up to 152 layers", "a quote that the abstract never had")},
    {"id": "bert", "purpose": facet("pre-train deep bidirectional language representations",
                                    "pre-trains deep bidirectional representations from unlabeled text"),
     "mechanism": facet("warm restarts of the learning rate schedule", "pre-trains deep bidirectional representations"),
     "evaluation": facet("state of the art on eleven NLP tasks", "new state-of-the-art results on eleven")},
]}


def written(cands):
    return {"ideas": [
        {"candidate": cands[0], "title": "残差线性探针", "idea": "把残差连接加进探针。", "hypothesis": "更稳",
         "outcome": "损失更低", "effort": "low", "risk": "无增益", "queries": ["residual linear probe schedule"]},
        {"candidate": cands[1], "title": "多任务探针评估", "idea": "在十一项任务上评估。", "hypothesis": "排名更稳",
         "outcome": "排名一致", "effort": "medium", "risk": "算力", "queries": ["linear probe evaluation many tasks"]}]}


def verdicts():
    return {"assessments": [
        {"id": "F1", "novelty": "known", "closest": ["R1"], "differentiation": "已有人做", "feasibility": 5, "impact": 2,
         "note": ""},
        {"id": "F2", "novelty": "novel", "closest": [], "differentiation": "", "feasibility": 4, "impact": 4, "note": ""}]}


def test_facets_are_checked_recombined_written_and_ranked(paper):
    with script_roles({wf.decomposer: [FACETS], wf.writer: [written(["C1", "C3"])],
                       ideas_wf.assessor: [verdicts()]}) as calls:
        out = json.loads(wf.facet_ideas(str(paper), count="2"))
    asked = calls[wf.decomposer][0].text
    assert "Warm restarts cut the validation loss" in asked and "residual learning framework" in asked
    assert "A Work Without Abstract" not in asked
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "facet_ideas"
    facets = json.loads((folder / "facets.json").read_text())
    assert [w["id"] for w in facets["works"]] == ["resnet", "bert"]               # cited order
    assert "evaluation" not in facets["works"][0]["facets"]                       # its quote is not in the abstract
    assert facets["dropped"][0]["work"] == "resnet" and facets["dropped"][0]["facet"] == "evaluation"
    cands = [(c["id"], c["swapped"], c["work"]) for c in facets["candidates"]]
    # bert's mechanism says the same as the project's and is skipped; round-robin over the works
    assert cands == [("C1", "purpose", "resnet"), ("C2", "purpose", "bert"), ("C3", "mechanism", "resnet"),
                     ("C4", "evaluation", "bert")]
    c3 = facets["candidates"][2]["facets"]
    assert c3["mechanism"]["source"] == "resnet" and c3["purpose"]["source"] == "PROJECT"
    writer_text = calls[wf.writer][0].text
    assert "Write up 2 ideas." in writer_text and "residual learning framework (from resnet)" in writer_text
    ideas = json.loads((folder / "ideas.json").read_text())
    assert [(i["id"], i["novelty"], i["rank"]) for i in ideas["ideas"]] == [("F2", "unverified", 1), ("F1", "known", 2)]
    f1 = next(i for i in ideas["ideas"] if i["id"] == "F1")
    assert f1["gaps"] == ["facet:mechanism:PROJECT", "facet:evaluation:PROJECT", "facet:purpose:resnet"]
    assert f1["closest"][0]["title"] == "Residual linear probes" and f1["elo"] == ideas_wf.ELO
    # every field idea_generation writes for an idea is here, so the lists can be appended
    needed = {"id", "title", "hypothesis", "approach", "outcome", "effort", "risk", "gaps", "queries", "elo", "wins",
              "losses", "beat", "lost_to", "novelty", "closest", "differentiation", "feasibility", "impact", "note", "rank"}
    assert needed <= set(f1) and set(ideas) == {"status", "topics", "unavailable_sources", "ideas"}
    assert ideas_wf.render(ideas)                                                 # idea_generation's renderer reads it
    assert out["status"] == "PASS_WITH_NOTES" and out["candidates"] == 4
    report = (folder / "ideas.md").read_text()
    assert "机制（resnet（Deep Residual Learning for Image Recognition））" in report or "resnet" in report
    assert "“tune the learning-rate schedule of linear probes”" in report and "no abstract found for 1 works" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "decompose", "recombine", "write", "search", "assess",
                                                     "rank"]


def test_writer_replies_are_checked(paper):
    ids = ["C1", "C2", "C3"]
    with pytest.raises(ValueError, match="twice"):
        wf.check_ideas(written(["C1", "C1"]), ids, 2)
    with pytest.raises(ValueError, match="not 3"):
        wf.check_ideas(written(["C1", "C2"]), ids, 3)
    with pytest.raises(ValueError, match="count"):
        wf.facet_ideas(str(paper), count="9")


def test_no_abstract_is_a_fail_not_a_crash(paper, monkeypatch):
    monkeypatch.setattr(wf, "abstracts", lambda entries: {})
    out = json.loads(wf.facet_ideas(str(paper)))
    assert out["status"] == "FAIL" and "no work of the .bib has an abstract" in Path(out["report"]).read_text()


def test_abstracts_from_semantic_scholar_openalex_and_arxiv(monkeypatch):
    sent = []

    def post(url, params, body, label="", headers=None):
        sent.append(body["ids"])
        return [None, {"abstract": "From S2."}]
    monkeypatch.setattr(ab, "post", post)
    monkeypatch.setattr(sources, "fetch", lambda url, params=None, **k: (
        {"abstract_inverted_index": {"From": [0], "OpenAlex.": [1]}} if "doi:10.1/x" in url else None))
    monkeypatch.setattr(search, "openalex_search", lambda q, n: [search.candidate("Titled Only Work", "By title.")])
    monkeypatch.setattr(search, "arxiv_ids", lambda ids: [search.candidate("A", "From arXiv.", arxiv="2001.00001")])
    entries = [{"key": "a", "arxiv": "2001.00001", "doi": "", "title": "A"},
               {"key": "b", "arxiv": "2002.00002", "doi": "", "title": "B"},
               {"key": "c", "arxiv": "", "doi": "10.1/x", "title": "C"},
               {"key": "d", "arxiv": "", "doi": "", "title": "Titled only work"}]
    found = ab.abstracts(entries)
    assert sent == [["ARXIV:2001.00001", "ARXIV:2002.00002", "DOI:10.1/x"]]
    assert found == {"b": ("From S2.", "Semantic Scholar"), "c": ("From OpenAlex.", "OpenAlex"),
                     "d": ("By title.", "OpenAlex"), "a": ("From arXiv.", "arXiv")}
