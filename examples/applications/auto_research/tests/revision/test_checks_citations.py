"""Cross-references, citations, bibliography."""

import importlib

from .helpers import doc, levels, make

citations = importlib.import_module("auto_research.tasks.revision.steps.checks.citations")

BIB = "@article{a,\n title={Same},\n journal={arXiv preprint}\n}\n@inproceedings{b,\n title={Same},\n}\n"


def test_references_and_citations():
    paper = make(doc("As fig.~\\ref{fig:a} and Figure \\ref{fig:a} show, text\\citep{k} holds. \\citep{k} shows it."))
    found = levels(citations.references(paper) + citations.citations(paper))
    assert ("FAIL", "ref-form") in found and ("WARN", "ref-form") in found
    assert ("FAIL", "cite-space") in found and ("FAIL", "cite-subject") in found


def test_cite_keys_and_bib_entries():
    paper = make(doc("\\citep{a,c}"), draft_bib={"references.bib": BIB.encode()})
    assert levels(citations.cite_keys(paper)) == [("FAIL", "cite-keys")]
    assert sorted(levels(citations.bib_entries(paper))) == [("WARN", "bib-arxiv"), ("WARN", "bib-duplicate")]


def test_bib_change_fails():
    changed = make(base_bib={"references.bib": b"a"}, draft_bib={"references.bib": b"b"})
    assert levels(citations.bib_unchanged(changed)) == [("WARN", "bib")]
    assert citations.bib_unchanged(make(base_bib={"references.bib": b"a"}, draft_bib={"references.bib": b"a"})) == []
