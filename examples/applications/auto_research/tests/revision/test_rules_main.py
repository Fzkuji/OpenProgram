"""Without rules.json naming them, the main .tex and the .bib files are found in the repository."""

import json

from auto_research.tasks.revision.rules import paper as rules


def test_main_and_bib_are_found_when_rules_json_names_none(tmp_path):
    (tmp_path / "paper.tex").write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    (tmp_path / "macros.tex").write_text("\\newcommand{\\x}{y}\n")
    (tmp_path / "refs.bib").write_text("@x{a}\n")
    got = rules.load(tmp_path)
    assert got.main_tex == "paper.tex" and got.bib_files == ["refs.bib"]
    (tmp_path / "workflow").mkdir()
    (tmp_path / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "other.tex"}))
    assert rules.load(tmp_path).main_tex == "other.tex"
