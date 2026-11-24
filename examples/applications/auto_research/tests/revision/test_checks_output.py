"""Build log, page limit, appendix build, Type 3 fonts, comparison PDF."""

import importlib

from .helpers import levels, make

output = importlib.import_module("auto_research.tasks.revision.steps.checks.output")


def test_build_log_findings():
    log = "Overfull \\hbox (1pt too wide)\nLaTeX Warning: Reference `fig:x' on page 3 undefined"
    assert levels(output.build(make(log=log))) == [("FAIL", "overfull"), ("FAIL", "references")]
    assert levels(output.build(make(built=False))) == [("FAIL", "build")]


def test_pages_counts_body_before_references():
    assert output.pages(make(page_texts=["a", "b", "c", "d", "e", "References\nx"])) == []
    assert levels(output.pages(make(page_texts=["a"] * 6 + ["References\nx"]))) == [("FAIL", "pages")]
    assert levels(output.pages(make(page_texts=["a", "b"]))) == [("FAIL", "pages")]


def test_appendix_fonts_and_diff():
    paper = make(extras=[{"name": "0main_appendix.tex", "built": False, "log": ""}],
                 type3={"draft.pdf": ["T3Font"]}, diff_ok=False, diff_message="latexdiff failed")
    assert levels(output.appendix(paper)) == [("FAIL", "appendix-build")]
    assert levels(output.type3(paper)) == [("FAIL", "type3")]
    assert levels(output.diff(paper)) == [("WARN", "diff")]
