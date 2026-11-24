"""Check a paper revision before it is shown to the author or written into the paper.

Steps: prepare (build and read both versions), checks (deterministic rules),
review (the reviewer model reads paragraphs against the writing rules),
report (one row per guide item, saved with the PDFs).
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from openprogram import Agent, Context
from openprogram.runtime.agent.call_state import check_cancelled
from openprogram.models.providers.structured_output import JsonSchemaOutput

from auto_research.trail import Trail

from .rules import checklist, writing
from .rules import paper as paper_rules
from .steps import checks, report
from .steps.checks.base import finding
from .steps.prepare import plans, prepare, section_matches, section_titles
from .steps.prepare.source import sectioned_paragraphs

reviewer = Agent(
    instructions="You review academic writing against a lab's written rules. "
                 "You report problems; you never rewrite the paper.",
    tools=[],
    effort="high",
)
ROLES = {"reviewer": reviewer}


def paper_revision_check(paper_dir: str, draft_dir: str = "", baseline: str = "HEAD",
                         approved_removals: str = "", scope: str = "changes", plan_dir: str = "",
                         phases: str = "", sections: str = "") -> str:
    """Run every check on a proposed paper revision and save a report with a red/green PDF.

    paper_dir is the paper's Git repository. draft_dir holds the proposed revision;
    when empty, the working tree of paper_dir is checked. baseline is the Git revision
    to compare against, or WORKTREE for the current working tree of paper_dir. approved_removals lists float labels the author agreed to remove,
    separated by commas. scope "changes" applies the wording rules and the reviewer to
    added paragraphs only; "paper" applies them to every paragraph. plan_dir holds
    section plan cards; the reviewer reads each paragraph against the approved card of
    its section. phases picks checklist phases by number ("4,6"; empty runs all 8).
    sections picks sections by title or title start ("Workflow Evolution"; empty checks
    all): the paragraph checks and the reviewer read only those sections, while the
    paper-wide checks (build, pages, floats, LaTeX, bibliography) follow phases alone.
    The paper sources are never modified.
    """
    if scope not in ("changes", "paper"):
        raise ValueError('scope must be "changes" or "paper"')
    paper_path = Path(paper_dir).expanduser().resolve()
    if not (paper_path / ".git").exists():
        raise ValueError(f"{paper_path} is not a Git repository")
    draft_source = Path(draft_dir).expanduser().resolve() if draft_dir else paper_path
    main_tex = paper_rules.load(paper_path).main_tex
    if not (draft_source / main_tex).exists():
        raise ValueError(f"{draft_source / main_tex} does not exist")
    rows = checklist.select(phases)
    chosen_sections = [s.strip() for s in sections.split(",") if s.strip()]
    allowed = [x.strip() for x in approved_removals.split(",") if x.strip()]
    if plan_dir and not Path(plan_dir).expanduser().is_dir():
        raise ValueError(f"{plan_dir} is not a folder")
    cards = plans.load(Path(plan_dir).expanduser()) if plan_dir else {}
    out = paper_path / "drafts" / "checks" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True)
    trail = Trail("paper_revision_check", {"paper_dir": str(paper_path), "draft_dir": str(draft_source),
                                           "baseline": baseline, "approved_removals": allowed, "scope": scope,
                                           "plan_dir": plan_dir, "phases": phases, "sections": chosen_sections},
                  ROLES)

    with tempfile.TemporaryDirectory() as tmp:
        paper = prepare(paper_path, baseline, draft_source, Path(tmp), out, allowed, scope)
        titles = section_titles(paper.draft_flat)
        unknown = [s for s in chosen_sections if not any(section_matches(t, [s]) for t in titles)]
        if unknown:
            raise ValueError(f"no section titled {', '.join(unknown)}; sections are {'; '.join(titles)}")
        paper.sections = chosen_sections
        check_cancelled()
        added = paper.added
        trail.step("prepare", baseline=baseline, draft=str(draft_source), added_paragraphs=len(added),
                   plan_cards=sorted(cards), phases=sorted({r[0] for r in rows}), sections=chosen_sections)
        wanted = checklist.checks_of(rows)
        findings = [f for f in checks.run(paper) if f["check"] in wanted]
        trail.step("checks", findings={level: sum(f["level"] == level for f in findings)
                                       for level in ("FAIL", "WARN", "REVIEW")},
                   checks=sorted({f["check"] for f in findings}))
        numbers = checklist.rules_of(rows)
        chosen = [(t, p) for t, p in sectioned_paragraphs(paper.draft_flat)
                  if (scope == "paper" or p in set(added))
                  and (not chosen_sections or section_matches(t, chosen_sections))]
        check_cancelled()
        if numbers and chosen:
            findings += review(chosen, cards, trail, paper.rules.writing_rules, numbers)

    return report.save(out, findings, added, baseline, draft_source, scope, trail, rows, chosen_sections)


def review(paragraphs: list[tuple[str, str]], cards: dict | None = None, trail: Trail | None = None,
           paper_rules: str = "", numbers=None) -> list[dict]:
    """Read each paragraph against the writing rules, using its section title to decide
    which section rules apply. The paper rules are the author's rules for this paper;
    report a violation of one of them as rule 35. When a paragraph carries its section's
    plan card, check that the paragraph serves the card's purpose and claims nothing the
    card rules out. Report only real violations, quoting the sentence. Say what is wrong
    in plain words and how to fix it. Do not report taste. Do not rewrite whole paragraphs."""
    found = []
    trail = trail or Trail("paper_revision_check", {}, ROLES)
    for n, start in enumerate(range(0, len(paragraphs), REVIEW_BATCH), 1):
        batch = paragraphs[start:start + REVIEW_BATCH]
        check_cancelled()
        rules = writing.rules_text(numbers) if numbers else writing.WRITING_RULES
        ctx = Context({"writing rules": rules,
                       "paper rules": (paper_rules.strip() or "none") if not numbers or 35 in numbers else "none",
                       "paragraphs": [paragraph(i, t, p, cards or {}) for i, (t, p) in enumerate(batch)]})
        with trail.call(f"review batch {n}", reviewer, review, ctx, schema="paper_writing_review") as rec:
            rec.output = reviewer("List the rule violations.", context=ctx, response_format=JsonSchemaOutput(
                schema=REVIEW_SCHEMA, name="paper_writing_review",
                fallback="prompt", max_validation_retries=1,
            ))
        found += parse_review(rec.output, batch)
    return found


# Plain code below: no model calls.

REVIEW_BATCH = 15


def paragraph(i: int, section: str, text: str, cards: dict) -> dict:
    item = {"id": i, "section": section, "text": text}
    card = cards.get(plans.key(section))
    if card:
        item["section plan"] = plans.for_reviewer(card)
    return item

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["paragraph", "rule", "quote", "problem", "fix"],
        "properties": {
            "paragraph": {"type": "integer", "minimum": 0},
            "rule": {"type": "integer", "minimum": 1},
            "quote": {"type": "string"},
            "problem": {"type": "string"},
            "fix": {"type": "string"},
        },
    }}},
}


def parse_review(reply, batch: list[tuple[str, str]]) -> list[dict]:
    data = json.loads(reply) if isinstance(reply, str) else reply
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("Reviewer reply is not an issue list")
    found = []
    for issue in data["issues"]:
        if not 0 <= issue.get("paragraph", -1) < len(batch):
            raise ValueError(f"Reviewer cited an unknown paragraph: {issue}")
        section = batch[issue["paragraph"]][0]
        found.append(finding("REVIEW", f"rule {issue['rule']}",
                             f"[{section}] \"{issue['quote']}\" — {issue['problem']} Fix: {issue['fix']}"))
    return found
