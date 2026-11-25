"""text_overlap on a fixture paper with arXiv and Semantic Scholar replaced by a fake fetch."""

import json

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.overlap import prose, providers, text_overlap
from auto_research.tasks.survey import search

OWN = ("Our earlier system stores every observation in a flat episodic buffer and retrieves entries by cosine "
       "similarity over sentence embeddings, which works well for short tasks but degrades sharply once the buffer "
       "holds more than a few thousand entries because unrelated memories crowd out the relevant ones.")
OWN_MORE = ("We measured this effect on three household benchmarks and found that success rates fell by roughly "
            "half between the smallest and the largest buffer sizes, while the time spent in retrieval grew "
            "linearly with the number of stored entries and soon dominated the total cost of every episode that "
            "the agent attempted in our experiments.")
ARX = ("Hierarchical summarization compresses each completed episode into a short abstract that is indexed "
       "separately from raw observations, so that the retriever first selects a handful of relevant episodes and "
       "only then searches their detailed contents, which keeps retrieval latency nearly constant as the memory "
       "grows over thousands of interactions.")
COMMON = "In this paper we propose a novel method for"

MAIN = r"""\documentclass{article}
\title{Adaptive Memory for Long Horizon Agents}
\begin{document}
\maketitle
\begin{abstract}
@COMMON@ long horizon agents that forget on purpose. % a comment with copied words would not count
\end{abstract}
\section{Introduction}
\input{sections/intro}
\section{Method}
Our memory keeps a budget $B$ of entries and evicts the least useful one, where usefulness
is estimated from how often an entry changed a decision $u_i = \sum_t c_{i,t}$.
\begin{equation}
u_i = \frac{1}{T}\sum_t c_{i,t}
\end{equation}
Unlike prior designs, eviction is learned from the agent's own failures rather than fixed by hand.
\bibliography{references}
\end{document}
"""
BIB = r"""@article{smith, title={Hierarchical Episodic Memory for Agents}, author={Smith, A.}, eprint={2401.11111}, year={2024}}
@article{plain, title={A Paper Not On Arxiv}, author={Doe, J.}, doi={10.1/x}, year={2020}}
"""


def feed(*entries):
    items = "".join(f"<entry><id>http://arxiv.org/abs/{i}v1</id><title>{t}</title><summary>{s}</summary>"
                    f"<published>2024-01-01T00:00:00Z</published><author><name>A Smith</name></author></entry>"
                    for i, t, s in entries)
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{items}</feed>'


SMITH = ("2401.11111", "Hierarchical Episodic Memory for Agents",
         f"{COMMON} agents that remember too much. We study memory at scale.")
HTML = (f'<html><body><section id="S1"><h2 class="ltx_title">1 Introduction</h2><p>Agents need memory. {ARX} '
        f'We then evaluate it.</p></section><section id="S2"><h2 class="ltx_title">2 Method</h2>'
        f"<p>The summarizer is a small model trained on episode logs.</p></section></body></html>")


@pytest.fixture(autouse=True)
def no_service_keys(monkeypatch):
    """A key in the developer's environment must not send fixture papers anywhere."""
    for p in providers.PROVIDERS.values():
        for name in p.env:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def offline(monkeypatch):
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        calls.append(url)
        if url == "https://export.arxiv.org/api/query":
            if params.get("id_list") == "2401.11111":
                return feed(SMITH)
            if "Adaptive" in params.get("search_query", ""):      # the paper itself is on arXiv
                return feed(("2501.22222", "Adaptive Memory for Long Horizon Agents", f"{COMMON} forgetting."))
            return feed()
        if url == "https://arxiv.org/html/2401.11111":
            return HTML
        if url.startswith("https://api.semanticscholar.org/"):
            return {"data": [{"paperId": "s1", "title": "Forgetting in Agents", "abstract": "Agents can forget.",
                              "year": 2024, "externalIds": {}, "authors": []}]}
        return None

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "fetch", fetch)
    monkeypatch.setattr(search, "ARXIV_GAP", 0)
    return calls


def make(tmp_path, own=OWN, cite=r"~\citep{smith}", note=""):
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "workflow" / "own_papers").mkdir(parents=True)
    (repo / "0main.tex").write_text(MAIN.replace("@COMMON@", COMMON))
    (repo / "sections" / "intro.tex").write_text(
        "Agents that run for days accumulate far more experience than they can use.\n\n"
        f"{own}\nThis motivates a memory that decides what to keep.\n\n"
        f"{ARX[:-1]}{cite}.\nWe build on this idea but learn what to forget.\n")
    (repo / "references.bib").write_text(BIB)
    (repo / "workflow" / "own_papers" / "earlier.txt").write_text(
        "Abstract. We present a buffer.\n" + OWN + " " + OWN_MORE + "\nConclusion follows.")
    if note:
        (repo / "workflow" / "text_overlap.md").write_text(note)
    return repo


def run(repo):
    out = json.loads(text_overlap(str(repo)))
    return out, json.loads(open(out["json"]).read())


def test_finds_own_and_cited_arxiv_passages(tmp_path, offline):
    out, data = run(make(tmp_path))
    by_kind = {m["source"]["kind"]: m for m in data["matches"]}
    assert set(by_kind) == {"own", "reference"}
    own, ref = by_kind["own"], by_kind["reference"]
    assert own["location"] == "sections/intro.tex:3" and own["overlap"] == 100.0 and not own["cited"]
    assert own["source"]["file"] == "workflow/own_papers/earlier.txt" and own["words"] < 100
    assert ref["location"] == "sections/intro.tex:6" and ref["cited"] and not ref["quoted"]
    assert ref["source"]["url"] == "https://arxiv.org/abs/2401.11111" and ref["source"]["level"] == "full"
    # cited, and the own passage is under 100 words: noted, not failed
    assert out["status"] == "PASS_WITH_NOTES" and not data["problems"]
    assert {s["kind"] for s in data["sources"]} == {"own", "reference", "search"}
    # the paper itself, found by the title search, is not a source
    assert all("Adaptive" not in s["title"] for s in data["sources"])
    report = open(out["report"]).read()
    assert "iThenticate" in report and "sections/intro.tex:6" in report
    assert (tmp_path / "paper" / "drafts" / "text_overlap" / "cache" / "2401.11111.txt").is_file()
    assert set(out) == {"status", "report", "json", "run", "index", "matches", "sources", "unavailable_sources",
                        "commercial"}
    # no key: the commercial check is skipped, named, and leaves the paper alone
    assert out["commercial"]["status"] == "not_configured" and len(out["commercial"]["not_configured"]) == 3
    assert not (tmp_path / "paper" / "workflow" / "text_overlap.md").exists()
    assert "商业查重库" in report and "not configured" in report


def test_uncited_copy_of_another_work_fails(tmp_path, offline):
    out, data = run(make(tmp_path, cite=""))
    assert out["status"] == "FAIL"
    assert any("without a citation" in p for p in data["problems"])


def test_quoted_passage_is_marked(tmp_path, offline):
    repo = make(tmp_path)
    intro = repo / "sections" / "intro.tex"
    intro.write_text(intro.read_text().replace(ARX[:-1], r"\enquote{" + ARX[:-1] + "}"))
    _, data = run(repo)
    ref = next(m for m in data["matches"] if m["source"]["kind"] == "reference")
    assert ref["quoted"] and ref["cited"]


def test_own_paper_over_100_words_needs_a_note(tmp_path, offline):
    long = OWN + " " + OWN_MORE
    out, data = run(make(tmp_path, own=long))
    own = next(m for m in data["matches"] if m["own"])
    assert own["words"] > 100 and not own["noted"]
    assert out["status"] == "FAIL" and "own paper" in data["problems"][0]

    out, data = run(make(tmp_path / "noted", own=long,
                         note="- earlier.txt: the journal version extends this workshop paper.\n"))
    assert out["status"] == "PASS_WITH_NOTES" and next(m for m in data["matches"] if m["own"])["noted"]


def test_index_is_matched_over_total_words(tmp_path, offline):
    _, data = run(make(tmp_path))
    total = sum(len(s["norm"]) for s in prose.paper_sentences(tmp_path / "paper", "0main.tex"))
    assert data["total_words"] == total
    assert data["matched_words"] == sum(m["words"] for m in data["matches"])
    assert data["index"] == round(data["matched_words"] / total, 4)
    assert 0.3 < data["index"] < 0.7


def test_short_common_phrase_is_no_match(tmp_path, offline):
    repo = make(tmp_path, own="We keep our own words here.", cite="")
    intro = repo / "sections" / "intro.tex"
    intro.write_text(intro.read_text().replace(ARX[:-1], "Retrieval should stay fast as memory grows"))
    out, data = run(repo)
    # COMMON is in the abstract of the paper and of the arXiv source: eight shared words, no match
    assert data["matches"] == [] and out["status"] == "PASS"
    assert out["index"] == 0


def test_prose_drops_math_comments_and_citations(tmp_path):
    repo = make(tmp_path)
    found = prose.paper_sentences(repo, "0main.tex")
    text = " ".join(" ".join(s["words"]) for s in found)
    assert "comment" not in text and "frac" not in text and "citep" not in text and "smith" not in text
    method = next(s for s in found if s["words"][:2] == ["Our", "memory"])
    assert method["file"] == "0main.tex" and method["line"] == 11
    assert prose.title(repo, "0main.tex") == "Adaptive Memory for Long Horizon Agents"


def test_missing_own_paper_raises(tmp_path, offline):
    with pytest.raises(ValueError, match="does not exist"):
        text_overlap(str(make(tmp_path)), own_papers="nope.pdf")
