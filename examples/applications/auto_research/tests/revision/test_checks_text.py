"""Wording of added paragraphs, new numbers, deletions, paper-wide wording, last lines."""

import importlib

from .helpers import doc, levels, make

text = importlib.import_module("auto_research.tasks.revision.steps.checks.text")
venue = importlib.import_module("auto_research.tasks.revision.rules.venue")


def test_style_rules_apply_to_added_text_only():
    old = "The workflow keeps the original sentence about archived versions here."
    paper = make(doc(old, "Table~\\ref{tab:x} reports the ratings of the agent in all three tasks. "
                          "We ask whether gains, losses, ties, and draws matter in this setting. "
                          "The main branch holds 359 commits in total for this project."), doc(old))
    assert len(paper.added) == 1
    found = levels(text.style(paper))
    assert ("FAIL", "opening") in found and ("WARN", "commas") in found and ("WARN", "wording") in found
    assert found.count(("FAIL", "term")) == 2


def test_openers_and_colons():
    found = levels(text.sentence_style("Once the agent acts, it learns the rule: it is short; it is clear."))
    assert ("WARN", "opener") in found and ("WARN", "wording") in found


def test_new_numbers_are_listed():
    paper = make(doc("Ratings reach 2760 in Bargaining and rise by 754 points over development."),
                 doc("Ratings reach 2760 in Bargaining at the end of the competition."))
    [item] = text.numbers(paper)
    assert "754" in item["message"] and "2760" not in item["message"]


def test_deleted_paragraphs_ignore_layout_commands():
    base = doc("The first paragraph stays exactly as it was here.\n\n\\section{End} The end paragraph is here.")
    moved = doc("The first paragraph stays exactly as it was here.\n\n\\FloatBarrier\n\\section{End} The end paragraph is here.")
    assert text.deleted(make(moved, base)) == []
    assert levels(text.deleted(make(doc("Only new text is written in this draft now."), base))) == [("WARN", "deleted")] * 2


def test_paper_wide_wording():
    paper = make(doc("\\subsection{results of the model}\nThis always works [TBD], is obvious and SOTA. "
                     "\\textbf{this whole long sentence is set in bold type here}"))
    found = levels(text.placeholders(paper) + text.absolute(paper) + text.claims(paper)
                   + text.bold(paper) + text.headings(paper))
    assert ("FAIL", "placeholder") in found and found.count(("WARN", "absolute")) == 2
    assert ("WARN", "sota") in found and ("WARN", "bold") in found and ("WARN", "heading-case") in found


def test_last_line_rules():
    left = (612 - venue.TEXT_WIDTH_PT) / 2
    right = left + venue.TEXT_WIDTH_PT

    def line(y, x0, x1, words):
        return {"x0": x0, "y0": y, "x1": x1, "y1": y + 8, "words": [(0, 0, 0, 0, w) for w in words.split()]}

    page = {"width": 612, "height": 792, "lines": [
        line(100, left, right, "a full justified line of text"), line(110, left, left + 40, "two words."),
        line(130, left, right, "another full line here"), line(140, left, left + 100, "one two three four."),
        line(160, left, right, "• Item opens at the margin"), line(170, left + 8, right, "item text continues indented"),
        line(180, left + 8, left + 30, "adds them."),
        line(200, left, right, "a last full paragraph line"), line(210, left, right - 100, "a line that fills most of the width"),
        line(740, left, left + 60, "Footer Track.")]}
    paper = make(lines=[page], page_texts=["body", "References"])
    found = text.last_lines(paper)
    assert [f["message"].split(":")[1].strip()[:20] for f in found] == [
        "last line has 2 word", "last line has 4 word", "last line has 2 word"]
    assert {f["level"] for f in found} == {"FAIL"}


def test_expression_rules_from_writing_guides():
    old = "Old text stays here as it was before."
    paper = make(doc(old, "In recent years, agents have attracted attention. This shows a pivotal shift that paves "
                          "the way for very robust systems. It is worth noting that we perform an analysis. "
                          "Moreover, it serves as a base. Furthermore, the results prove the point."), doc(old))
    found = {f["check"] for f in text.expression(paper)}
    assert {"formulaic-opener", "vague-this", "ai-words", "hype", "intensifier", "boilerplate",
            "nominalization", "copula", "connectives", "overclaim"} <= found


def test_long_sentences_and_scope():
    long = "The workflow " + " ".join(["keeps"] * 32) + " going."
    paper = make(doc("Short old text stays the same here.", long), doc("Short old text stays the same here."))
    assert [f["check"] for f in text.expression(paper)] == ["long-sentence"]
    whole = make(doc("A very short line of old text."), scope="paper")
    assert [f["check"] for f in text.expression(whole)] == ["intensifier"]
