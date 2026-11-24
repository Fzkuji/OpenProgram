"""Plan cards: parsing, completeness and approval."""

import importlib

plans = importlib.import_module("auto_research.tasks.revision.steps.prepare.plans")

CARD = """Section: Workflow Evolution (RQ2)
Claims: C2, C3
Evidence: E1
Approved: yes

## Purpose
Show continuous revision.

## What the evidence shows
Changes from iteration 12 to 689.

## What it cannot claim
Per-change rating causes.

## Storyline
1. Continuous.
"""


def test_card_is_parsed():
    card = plans.parse(CARD)
    assert card["Section"] == "Workflow Evolution (RQ2)"
    assert plans.ids(card["Claims"]) == ["C2", "C3"]
    assert card["What it cannot claim"] == "Per-change rating causes."
    assert plans.approved(card) and plans.problems(card) == []


def test_incomplete_card_lists_missing_parts():
    card = plans.parse(CARD.replace("## Storyline\n1. Continuous.\n", "").replace("Evidence: E1", "Evidence:"))
    assert set(plans.problems(card)) == {"missing Evidence", "missing Storyline"}


def test_load_keeps_only_approved_complete_cards(tmp_path):
    (tmp_path / "a.md").write_text(CARD)
    (tmp_path / "b.md").write_text(CARD.replace("Approved: yes", "Approved: no").replace("(RQ2)", "(RQ3)"))
    assert list(plans.load(tmp_path)) == ["workflow evolution (rq2)"]
