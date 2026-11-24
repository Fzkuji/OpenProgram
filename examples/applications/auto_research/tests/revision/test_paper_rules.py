"""A paper's workflow/rules.json overrides the defaults; checks and the reviewer read it."""

import importlib
import json

import pytest

from .helpers import levels, make

paper_rules = importlib.import_module("auto_research.tasks.revision.rules.paper")
venue_checks = importlib.import_module("auto_research.tasks.revision.steps.checks.venue")
text_checks = importlib.import_module("auto_research.tasks.revision.steps.checks.text")


def write(tmp_path, data):
    (tmp_path / "workflow").mkdir(exist_ok=True)
    (tmp_path / "workflow" / "rules.json").write_text(json.dumps(data))
    return paper_rules.load(tmp_path)


def test_missing_file_keeps_the_defaults(tmp_path):
    rules = paper_rules.load(tmp_path)
    assert rules.main_tex == "0main.tex" and rules.body_page_limit == 5


def test_file_overrides_and_rejects_unknown_keys(tmp_path):
    rules = write(tmp_path, {"_doc": "x", "body_page_limit": 9, "required_authors": ["A. Person"]})
    assert rules.body_page_limit == 9 and rules.required_authors == ["A. Person"]
    assert rules.bib_files == ["references.bib"]
    with pytest.raises(ValueError, match="unknown keys"):
        write(tmp_path, {"page_limit": 4})


def test_checks_read_the_paper_rules(tmp_path):
    rules = write(tmp_path, {"required_authors": ["A. Person"], "required_source": [], "required_sections": [],
                             "banned_terms": {"widget": "Say component."}, "preferred_terms": {"gadget": "tool"}})
    paper = make("A widget and a gadget work together here.", main_source="\\author{A. Person\n}",
                 rules=rules, scope="paper")
    assert venue_checks.authors(paper) == [] and venue_checks.format_(paper) == []
    assert levels(text_checks.style(paper)) == [("FAIL", "term"), ("WARN", "term-preferred")]
