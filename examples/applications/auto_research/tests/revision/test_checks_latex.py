"""Spacing, quotes, e.g., math and itemize."""

import importlib

from .helpers import doc, levels, make

latex = importlib.import_module("auto_research.tasks.revision.steps.checks.latex")


def test_quotes_eg_and_spacing():
    paper = make(doc('We say "hello" (see A)and use e.g. this, but $f(x)y$ and \\texttt{g(x)y} are fine.'))
    found = levels(latex.punctuation(paper))
    assert ("FAIL", "quotes") in found and ("FAIL", "eg-ie") in found
    assert found.count(("WARN", "spacing")) == 1


def test_math_and_itemize():
    paper = make(doc("Here \\(x\\) and \\mathbf{v}.\n\\begin{equation}\na=b.\n\\end{equation}\n"
                     "\\begin{itemize}\n\\item a\n\\end{itemize}"))
    found = levels(latex.math(paper) + latex.itemize(paper))
    assert ("FAIL", "math-delim") in found and ("WARN", "boldsymbol") in found
    assert ("WARN", "display-punct") in found and ("WARN", "itemize") in found
