"""Parsing and matching, offline."""

from auto_research.tasks.citations.refs import assess, author_overlap, parse_bib, title_sim

BIB = r"""
@inproceedings{a2024x,
  title={{DSPy}: Compiling Declarative Language Model Calls},
  author={Khattab, Omar and Singhvi, Arnav and others},
  booktitle={International Conference on Learning Representations},
  year={2024}
}
@article{b, title="Agent Workflow Memory", author={Zora Wang}, journal={arXiv preprint arXiv:2409.07429}, year=2024}
"""


def rec(title, authors, year, venue="", source="openalex"):
    return {"source": source, "title": title, "authors": authors, "year": year, "venue": venue, "doi": "", "url": ""}


def test_parse_bib_reads_fields_and_arxiv_id():
    a, b = parse_bib(BIB)
    assert a["title"] == "DSPy: Compiling Declarative Language Model Calls"
    assert a["authors"] == ["Omar Khattab", "Arnav Singhvi"]
    assert a["venue"] == "International Conference on Learning Representations"
    assert b["arxiv"] == "2409.07429" and b["year"] == "2024"


def test_similarity_ignores_case_and_braces():
    assert title_sim("{DSPy}: compiling", "DSPy: Compiling") == 1.0
    assert author_overlap(["Omar Khattab", "Arnav Singhvi"], ["O. Khattab", "Someone Else"]) == 0.5


def test_verdicts_and_notices():
    a = parse_bib(BIB)[0]
    good = assess(a, [rec(a["title"], ["Omar Khattab", "Arnav Singhvi"], 2024, "ICLR 2024", "openreview")])
    assert good["assessment"] == "authentic" and good["notices"] == []
    preprint = assess(a, [rec(a["title"], ["Omar Khattab", "Arnav Singhvi"], 2021, "arXiv", "arxiv")])
    assert preprint["notices"] == ["year", "venue_unconfirmed"]
    missing = assess(a, [rec("A survey of something else", ["X Y"], 2024)])
    assert missing["assessment"] == "inauthentic"
    assert assess(a, [])["assessment"] == "inauthentic"
