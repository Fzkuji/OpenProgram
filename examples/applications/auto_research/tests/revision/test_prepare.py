"""Reading sources and repairing the comparison PDF."""

import importlib

build = importlib.import_module("auto_research.tasks.revision.steps.prepare.build")
source = importlib.import_module("auto_research.tasks.revision.steps.prepare.source")


def test_changed_tabular_opening_keeps_the_new_line():
    text = ("\\DIFdelbeginFL %DIFDELCMD < \\begin{tabular*}{\\textwidth}{lr}\n%DIFDELCMD < %%%\n"
            "\\DIFdelendFL \\DIFaddbeginFL \\begin{tabular}{lr}\n\\DIFaddendFL \\toprule\n")
    assert build.keep_new_tabular(text) == "\\begin{tabular}{lr}\n\\toprule\n"


def test_abstract_and_section_titles_are_kept():
    flat = ("\\begin{document}\n\\maketitle\n\\begin{abstract}\nThe abstract has enough words here.\n"
            "\\end{abstract}\n\n\\section{Intro}\nThe introduction paragraph has words.\n\\bibliography{x}")
    assert source.sectioned_paragraphs(flat) == [
        ("Abstract", "The abstract has enough words here."),
        ("Intro", "The introduction paragraph has words.")]


def test_each_list_item_is_its_own_paragraph():
    flat = ("\\begin{document}\n\\section{R}\nThe lead sentence has enough words.\n"
            "\\begin{itemize}[leftmargin=*]\n\\item First item has enough words.\n"
            "\\item Second item has enough words.\n\\end{itemize}\n\\bibliography{x}")
    assert [p for _, p in source.sectioned_paragraphs(flat)] == [
        "The lead sentence has enough words.", "First item has enough words.", "Second item has enough words."]
