"""The checklist refers only to checks and rules that exist."""

import importlib
import re
from pathlib import Path

checklist = importlib.import_module("auto_research.tasks.revision.rules.checklist")
writing = importlib.import_module("auto_research.tasks.revision.rules.writing")
# Check names are string literals in the checks, or keys of writing.WORDING.
CHECK_SOURCE = "\n".join(p.read_text() for p in
                         (Path(__file__).parents[2] / "tasks" / "revision" / "steps" / "checks").glob("*.py"))
CHECK_SOURCE += "\n".join(f'"{name}"' for name in writing.WORDING)


def test_every_auto_item_names_checks_that_exist():
    for phase, source, number, item, how, detail in checklist.CHECKLIST:
        assert how in ("auto", "review", "manual", "built-in", "n/a"), item
        if how == "auto":
            assert detail, item
            for name in detail:
                assert f'"{name}"' in CHECK_SOURCE, f"{item}: no check emits {name}"


def test_every_review_item_names_a_writing_rule():
    numbers = {int(n) for n in re.findall(r"^(\d+)\.", writing.WRITING_RULES, re.M)}
    for phase, source, number, item, how, detail in checklist.CHECKLIST:
        if how == "review":
            assert set(detail) <= numbers, item
