"""Placement, order, captions, removals, tables, page of first mention."""

import importlib

from .helpers import FIG, doc, levels, make

floats = importlib.import_module("auto_research.tasks.revision.steps.checks.floats")


def test_bottom_and_default_placement_fail_but_appendix_is_free():
    flat = doc(FIG % ("b", "Ok.", "a"), r"\begin{table}" "\n\\caption{T.}\\label{tab:x}\n\\end{table}")
    assert levels(floats.placement(make(flat))) == [("FAIL", "placement")] * 2
    assert floats.placement(make(doc(FIG % ("!t", "Ok.", "a"), FIG % ("t", "Ok.", "b")))) == []
    assert floats.placement(make(doc(FIG % ("t", "A.", "a"), "\\appendix", FIG % ("H", "B.", "b")))) == []


def test_float_order_follows_first_mention():
    text = "We see Figure~\\ref{fig:b} first and then Figure~\\ref{fig:a} later in this text."
    assert ("FAIL", "order") in levels(floats.order(make(doc(text, FIG % ("t", "A.", "a"), FIG % ("t", "B.", "b")))))
    assert floats.order(make(doc(text, FIG % ("t", "B.", "b"), FIG % ("t", "A.", "a")))) == []


def test_unmentioned_float_fails_and_second_label_counts():
    assert levels(floats.order(make(doc("Nothing refers to it here at all.", FIG % ("t", "A.", "a"))))) == [("FAIL", "mention")]
    fig = (FIG % ("t", "A.", "a")).replace("\\end{figure}", "\\label{fig:alias}\n\\end{figure}")
    assert floats.order(make(doc("Only Figure~\\ref{fig:alias} is cited here.", fig))) == []


def test_caption_case_and_period():
    flat = doc("See Figure~\\ref{fig:a} for the result.", FIG % ("t", "lower case", "a"))
    assert levels(floats.captions(make(flat))) == [("WARN", "caption")] * 2
    assert floats.captions(make(doc(FIG % ("t", "(a) Shares. (b) Times.", "a")))) == []


def test_removed_original_figure_fails_unless_approved():
    base = doc(FIG % ("t", "A.", "a"), FIG % ("t", "B.", "b"))
    draft = doc(FIG % ("t", "A.", "a"))
    assert levels(floats.removed(make(draft, base))) == [("FAIL", "removed")]
    assert floats.removed(make(draft, base, approved_removals=["fig:b"])) == []


def test_tables_and_caption_position():
    table = doc("\\begin{table}[t]\n\\small\n\\begin{tabular}{l|r}\n\\hline\na & b\n\\end{tabular}\n"
                "\\caption{T.}\\label{tab:x}\n\\end{table}")
    found = levels(floats.tables(make(table)) + floats.caption_position(make(table)))
    assert ("WARN", "booktabs") in found and ("WARN", "table-font") in found
    assert ("FAIL", "caption-position") in found


def test_same_page_uses_aux_pages():
    flat = doc("\\begin{figure}[t]\\caption{A.}\\label{fig:a}\\end{figure}")
    aux = "\\newlabel{fig:a}{{2}{4}{cap}{figure.2}{}}"
    late = make(flat, aux=aux, page_texts=["", "", "As Figure 2 shows", "Figure 2: A."])
    assert levels(floats.same_page(late)) == [("WARN", "same-page")]
    assert floats.same_page(make(flat, aux=aux, page_texts=["", "", "", "Figure 2 shows. Figure 2: A."])) == []


def test_figure_text_size_uses_printed_width():
    fig = "\\begin{figure}[t]\\includegraphics[width=0.5\\linewidth]{figures/a}\\caption{A.}\\end{figure}"
    small = {"figures/a": {"width": 400.0, "heights": [10.0] * 20}}
    paper = make(doc(fig), figures=small, body_word_height=10.0)
    assert levels(floats.figure_fonts(paper)) == [("WARN", "figure-font")]
    paper.figures = {"figures/a": {"width": 100.0, "heights": [10.0] * 20}}
    assert floats.figure_fonts(paper) == []
