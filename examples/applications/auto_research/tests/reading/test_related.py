"""related_by_paragraph with Semantic Scholar's search and recommendations replaced."""

import json

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import parse_bib
from auto_research.tasks.library import import_library
from auto_research.tasks.reading import reader, related
from auto_research.tasks.survey import search

MAIN = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Language agents plan long tasks with tool calls. Agent memory lets language agents reuse earlier tool calls,
and agent memory decides what a planner sees when it plans the next step of a long task \cite{yao2023react}.

\section{Related Work}
Retrieval augmented generation fetches documents for a question; retrieval augmented generation also grounds
long answers in fetched documents and reduces hallucination in question answering systems \cite{lewis2020}.

\section{Experiments}
We measure success rates of agents on many benchmarks and report the mean over five seeds for every agent here.
\bibliography{references}
\end{document}
"""
BIB = r"""@inproceedings{yao2023react, title={ReAct: Synergizing Reasoning and Acting in Language Models},
  author={Yao, Shunyu}, year={2023}, eprint={2210.03629}}
@inproceedings{lewis2020, title={Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks},
  author={Lewis, Patrick}, year={2020}, doi={10.5555/rag}}
"""


def s2(pid, title, year, abstract="", arxiv="", doi="", venue="ICLR", author="Ada Byron"):
    ext = {k: v for k, v in (("ArXiv", arxiv), ("DOI", doi)) if v}
    return {"paperId": pid, "title": title, "abstract": abstract, "year": year, "publicationDate": f"{year}-01-01",
            "externalIds": ext, "citationCount": 3, "venue": venue, "authors": [{"name": author}],
            "url": f"https://www.semanticscholar.org/paper/{pid}"}


MEMORY_OLD = s2("m1", "Agent Memory for Long Tasks", 2021, "Language agents store tool calls.", doi="10.9/mem")
MEMORY_NEW = s2("m2", "Planning Agents with Memory", 2024, "Language agents store tool calls of long tasks in agent memory.",
                arxiv="2405.00001", venue="")
ONE_TERM = s2("m3", "A Study of Memory", 2025, "Nothing else in common.")
IN_BIB = s2("r1", "ReAct: Synergizing Reasoning and Acting in Language Models", 2023, "Language agents plan.",
            arxiv="2210.03629")
RAG = s2("g1", "Grounded Answers by Retrieval", 2023, "Retrieval augmented generation reduces hallucination.",
         venue="ACL", author="Grace Hopper")


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex", "bib_files": ["references.bib"]}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "references.bib").write_text(BIB)
    seen = {"get": [], "post": [], "search": []}

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        if url == "https://api.semanticscholar.org/graph/v1/paper/search":
            seen["search"].append(params["query"])
            q = params["query"]
            return {"data": [MEMORY_OLD, ONE_TERM, IN_BIB] if "memory" in q else [RAG] if "retrieval" in q else []}
        if url.startswith("https://api.openalex.org/works"):
            seen.setdefault("openalex", []).append(params["search"])
            return {"results": []}
        if url.startswith(related.S2_REC + "/forpaper/"):
            seen["get"].append((url, params))
            return {"recommendedPapers": [MEMORY_NEW]}
        raise AssertionError(url)

    def post(url, params, payload, label="", headers=None, tries=3):
        seen["post"].append((url, params, payload))
        return {"recommendedPapers": [MEMORY_NEW, IN_BIB]}
    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "fetch", fetch)
    monkeypatch.setattr(reader, "post", post)
    monkeypatch.setattr(related, "S2_GAP", 0)
    return repo, seen


def test_query_is_plain_code():
    query, terms = related.make_query("Agent memory lets language agents reuse tool calls, and agent memory decides "
                                      "what language agents see.")
    assert query.startswith("agent memory language agents") and len(query.split()) <= 8
    assert terms[:3] == ["agent", "memory", "language"] and "and" not in terms and "what" not in terms


def test_suggestions_per_paragraph_from_cited_works(paper, tmp_path):
    repo, seen = paper
    out = json.loads(related.related_by_paragraph(str(repo)))
    assert out["status"] == "PASS" and out["paragraphs"] == 2              # experiments are not searched
    (url, params, payload), = seen["post"]
    assert url == "https://api.semanticscholar.org/recommendations/v1/papers"
    assert payload == {"positivePaperIds": ["ArXiv:2210.03629", "DOI:10.5555/rag"]}
    assert params["fields"] == search.S2_FIELDS and not seen["get"]
    record = json.load(open(out["json"], encoding="utf-8"))
    intro, rel = record["paragraphs"]
    assert intro["section"] == "Introduction" and "memory" in intro["query"]
    titles = [s["title"] for s in intro["suggestions"]]
    assert titles == ["Agent Memory for Long Tasks", "Planning Agents with Memory"]   # the top search hit first
    assert intro["phrases"] and set(intro["phrases"]) <= set(seen["openalex"])   # phrases searched in OpenAlex too
    assert all("ReAct" not in t for p in record["paragraphs"] for t in [s["title"] for s in p["suggestions"]])
    assert "A Study of Memory" not in titles                                         # one shared term is not enough
    assert [s["title"] for s in rel["suggestions"]] == ["Grounded Answers by Retrieval"]
    assert set(rel["suggestions"][0]["shared"]) >= {"retrieval", "augmented", "generation", "hallucination"}
    report = open(out["report"], encoding="utf-8").read()
    assert "## Introduction 第 1 段" in report and "共享术语" in report and "import_library" in report

    bib = open(out["bib"], encoding="utf-8").read()
    refs = {r["key"]: r for r in parse_bib(bib)}
    assert set(refs) == {"byron2024planning", "byron2021agent", "hopper2023grounded"}
    assert refs["byron2024planning"]["arxiv"] == "2405.00001" and refs["byron2021agent"]["doi"] == "10.9/mem"
    added = json.loads(import_library(str(repo), out["bib"]))["added"]
    assert sorted(added) == sorted(refs)
    again = json.loads(related.related_by_paragraph(str(repo), out_dir=str(tmp_path / "out")))
    assert again["candidates"] == 0                                               # now all in the .bib
    assert again["report"].startswith(str(tmp_path / "out"))


def test_own_arxiv_id_uses_forpaper(paper):
    repo, seen = paper
    out = json.loads(related.related_by_paragraph(str(repo), arxiv_id="arXiv:2401.00001v2"))
    (url, params), = seen["get"]
    assert url == related.S2_REC + "/forpaper/ArXiv:2401.00001" and params["from"] == "all-cs"
    assert not seen["post"] and out["candidates"] == 3


def test_failed_source_is_a_note(paper, monkeypatch):
    repo, _ = paper

    def down(url, params, payload, label="", headers=None, tries=3):
        sources.UNAVAILABLE.add(label)
    monkeypatch.setattr(reader, "post", down)
    out = json.loads(related.related_by_paragraph(str(repo)))
    assert out["status"] == "PASS_WITH_NOTES"
    assert out["unavailable_sources"] == ["Semantic Scholar recommendations"]
