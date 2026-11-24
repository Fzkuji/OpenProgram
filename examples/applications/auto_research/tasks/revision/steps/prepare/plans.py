"""Read section plan cards: what a section must show, from which evidence, and what it cannot claim.

A plan card is a Markdown file with header fields and four parts:

    Section: Workflow Evolution (RQ2)
    Claims: C2
    Evidence: E3, E4
    Approved: yes

    ## Purpose
    ## What the evidence shows
    ## What it cannot claim
    ## Storyline
"""

import re
from pathlib import Path

FIELDS = ("Section", "Claims", "Evidence", "Approved")
PARTS = ("Purpose", "What the evidence shows", "What it cannot claim", "Storyline")
FIELD = re.compile(r"^(" + "|".join(FIELDS) + r"):[ \t]*(.*)$", re.M)
PART = re.compile(r"^##\s+(.+?)\s*$", re.M)


def parse(text: str) -> dict:
    """Header fields and parts of one plan card. Missing parts are empty strings."""
    card = {name: "" for name in FIELDS + PARTS}
    for name, value in FIELD.findall(text):
        card[name] = value.strip()
    heads = list(PART.finditer(text))
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        if head.group(1) in PARTS:
            card[head.group(1)] = text[head.end():end].strip()
    return card


def ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def problems(card: dict) -> list[str]:
    """What keeps a card from being usable: a missing section, field or part."""
    missing = [name for name in ("Section", "Claims", "Evidence") if not card[name]]
    missing += [name for name in PARTS if not card[name]]
    return [f"missing {name}" for name in missing]


def approved(card: dict) -> bool:
    return card["Approved"].lower() in ("yes", "y", "true")


def key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def load(plan_dir: Path) -> dict[str, dict]:
    """Approved, complete plan cards in plan_dir, by normalized section title."""
    cards = {}
    for path in sorted(Path(plan_dir).glob("*.md")):
        card = parse(path.read_text(encoding="utf-8"))
        if approved(card) and not problems(card):
            cards[key(card["Section"])] = card
    return cards


def for_reviewer(card: dict) -> str:
    return "\n\n".join(f"{name}:\n{card[name]}" for name in PARTS)
