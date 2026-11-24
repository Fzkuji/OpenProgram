"""Camera-ready format, authors, file layout, code link, comment commands."""

import importlib

from .helpers import levels, make

venue_checks = importlib.import_module("auto_research.tasks.revision.steps.checks.venue")
venue = importlib.import_module("auto_research.tasks.revision.rules.venue")


def test_format_requirements():
    main = "\n".join(venue.REQUIRED_SOURCE)
    assert venue_checks.format_(make(main + "\n\\subsection{Agent Behavior Analysis (RQ3)}", main_source=main)) == []
    found = levels(venue_checks.format_(make("\\author{Anonymous Author(s)}", main_source="")))
    assert found.count(("FAIL", "format")) == 4


def test_authors_layout_link_and_macros():
    paper = make("plain text", main_source="\\author{Someone\n}\n\\input{intro}")
    assert len(venue_checks.authors(paper)) == len(venue.REQUIRED_AUTHORS)
    assert levels(venue_checks.file_layout(paper)) == [("WARN", "file-layout")]
    assert levels(venue_checks.code_link(paper)) == [("WARN", "code-link")]
    assert levels(venue_checks.comment_macros(paper)) == [("WARN", "comment-macros")]
