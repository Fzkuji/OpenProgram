"""Gate checks: what each task finds on disk in the paper's workflow/ folder.

Plain code, no model calls. Every artifact lives in <paper_dir>/workflow/. When a task
runs, a missing artifact is created from templates/ and reported, never overwritten. The
status view passes created=None: it reads a missing artifact as its template and writes
nothing.
"""

import csv
import io
import json
import re
from dataclasses import fields
from pathlib import Path

from .tasks.revision.rules.paper import Rules
from .tasks.revision.steps.prepare import plans

TEMPLATES = Path(__file__).parent / "templates"
FOLDER = "workflow"
VENUE_PARTS = ("Venue", "Requirements", "Submission checklist", "Camera-ready checklist")
# rules.json "version" -> the venue.md checklist of that version
CHECKLISTS = {"submission": "Submission checklist", "camera_ready": "Camera-ready checklist"}
EVIDENCE_COLUMNS = ("id", "claim", "statement", "source", "script")
CLAIM = re.compile(r"^(C\d+):[ \t]*(\S.*)$", re.M)
LITERATURE = re.compile(r"^-\s+([^\s:]+):", re.M)
BIB_KEY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")


def folder(paper: Path) -> Path:
    return paper / FOLDER


# The last result of each task run, kept by the entry and read by the status view.
STATE = Path("drafts") / "auto_research" / "state.json"


def load_state(paper: Path) -> dict:
    path = paper / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def template(name: str, **fields) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return text.format(**fields) if fields else text


def scaffold(paper: Path, name: str, created: list[str] | None, target: str = "", **fields) -> Path:
    """The artifact path; a missing artifact is copied from its template first, unless
    created is None (the status view), which writes nothing."""
    path = folder(paper) / (target or name)
    if not path.exists() and created is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template(name, **fields), encoding="utf-8")
        created.append(str(path.relative_to(paper)))
    return path


def read(paper: Path, name: str, created: list[str] | None, target: str = "", **fields) -> str:
    """The artifact's text; a missing one reads as its template."""
    path = scaffold(paper, name, created, target, **fields)
    return path.read_text(encoding="utf-8") if path.exists() else template(name, **fields)


def started(paper: Path, artifact: str) -> bool:
    """Whether the author has begun this artifact: it exists and is not just its template.
    A folder artifact ("plans/") counts once it holds a file that is not a template copy."""
    path = folder(paper) / artifact
    if artifact.endswith("/"):
        if not path.is_dir():
            return False
        return any(_own(f, folder(paper)) for f in path.rglob("*") if f.is_file() and not f.name.startswith("."))
    return path.is_file() and _own(path, folder(paper))


def _own(path: Path, root: Path) -> bool:
    copy = TEMPLATES / path.relative_to(root) if path.is_relative_to(root) else TEMPLATES / path.name
    if not copy.is_file():
        copy = TEMPLATES / path.name
    if not copy.is_file():
        return True
    try:
        return path.read_text(encoding="utf-8") != copy.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True


def rules_problems(text: str) -> list[str]:
    """What keeps workflow/rules.json from being usable: not JSON, unknown keys, or the
    venue values still at their template defaults."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"rules.json: not valid JSON ({e.msg} at line {e.lineno})"]
    if not isinstance(data, dict):
        return ["rules.json: must be an object"]
    known = {f.name for f in fields(Rules)}
    found = [f"rules.json: unknown key {k}" for k in sorted(set(data) - known - {"_doc"})]
    if not data.get("body_page_limit"):
        found.append("rules.json: body_page_limit not set")
    if data.get("version", "submission") not in CHECKLISTS:
        found.append(f"rules.json: version must be one of {', '.join(CHECKLISTS)}")
    if not isinstance(data.get("code", {}), dict):
        found.append('rules.json: code must be an object like {"repo": "...", "allowed_contributors": []}')
    items = data.get("deliverables", [])
    if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("name") and i.get("path") for i in items):
        found.append('rules.json: deliverables must be a list of {"name": ..., "path": ...}')
    if not data.get("required_source"):
        found.append("rules.json: required_source is empty; copy the style lines from the venue template")
    return found


def parts(text: str) -> dict[str, str]:
    heads = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.M))
    return {h.group(1): text[h.end():(heads[i + 1].start() if i + 1 < len(heads) else len(text))].strip()
            for i, h in enumerate(heads)}


def unchecked(text: str) -> list[str]:
    return re.findall(r"^-\s+\[ \]\s+(.+)$", text, re.M)


def bib_keys(paper: Path) -> set[str]:
    return {k for bib in paper.glob("*.bib") for k in BIB_KEY.findall(bib.read_text(encoding="utf-8", errors="ignore"))}


def survey(paper: Path, created: list[str] | None) -> list[str]:
    """Venue requirements written down; every surveyed work exists in the bibliography."""
    found = []
    venue = parts(read(paper, "venue.md", created))
    found += [f"venue.md: missing section {name}" for name in VENUE_PARTS if not venue.get(name)]
    found += rules_problems(read(paper, "rules.json", created))
    literature = read(paper, "literature.md", created)
    keys = [k for k in LITERATURE.findall(literature) if k != "key"]
    if not keys:
        found.append("literature.md: no surveyed work")
    known = bib_keys(paper)
    found += [f"literature.md: {k} is not in the .bib files" for k in keys if k not in known]
    return found


def claims(paper: Path, created: list[str] | None) -> tuple[list[str], dict[str, str]]:
    """Claims, each with the research question that tests it, approved by the author."""
    text = read(paper, "claims.md", created)
    found, table = [], dict(CLAIM.findall(text))
    if not table or "C1: The claim, stated as a result" in text:
        found.append("claims.md: no claims written")
    for cid in table:
        block = text[text.find(f"{cid}:"):]
        nxt = CLAIM.search(block, 1)
        if not re.search(r"^RQ:[ \t]*\S", block[:nxt.start() if nxt else len(block)], re.M):
            found.append(f"claims.md: {cid} has no RQ")
    if not plans.approved({"Approved": (re.search(r"^Approved:[ \t]*(.*)$", text, re.M) or [None, ""])[1]}):
        found.append("claims.md: not approved by the author")
    return found, table


def evidence(paper: Path, created: list[str] | None, claim_ids) -> tuple[list[str], dict[str, dict]]:
    """Every claim has evidence; every piece of evidence names an existing source."""
    with io.StringIO(read(paper, "evidence.csv", created), newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in EVIDENCE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            return [f"evidence.csv: missing columns {', '.join(missing)}"], {}
        rows = {r["id"].strip(): r for r in reader if r["id"].strip()}
    found = []
    for eid, row in rows.items():
        if row["claim"].strip() not in claim_ids:
            found.append(f"evidence.csv: {eid} names unknown claim {row['claim']}")
        for column in ("source", "script"):
            value = row[column].strip()
            if column == "source" and not value:
                found.append(f"evidence.csv: {eid} has no source")
            elif value and not (paper / value).exists():
                found.append(f"evidence.csv: {eid} {column} {value} does not exist")
    covered = {r["claim"].strip() for r in rows.values()}
    found += [f"evidence.csv: claim {c} has no evidence" for c in claim_ids if c not in covered]
    return found, rows


def slug(section: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-") or "section"


def plan(paper: Path, section: str, created: list[str] | None, claim_ids, evidence_ids) -> list[str]:
    """The section's plan card is complete, cites known claims and evidence, and is approved."""
    path = folder(paper) / "plans" / f"{slug(section)}.md"
    card = plans.parse(read(paper, "plan.md", created, target=f"plans/{slug(section)}.md", section=section))
    found = [f"plan {path.name}: {p}" for p in plans.problems(card)]
    found += [f"plan {path.name}: unknown claim {c}" for c in plans.ids(card["Claims"]) if c not in claim_ids]
    found += [f"plan {path.name}: unknown evidence {e}" for e in plans.ids(card["Evidence"]) if e not in evidence_ids]
    if not plans.approved(card):
        found.append(f"plan {path.name}: not approved by the author")
    return found


def checklist(paper: Path, part: str, created: list[str] | None) -> list[str]:
    venue = parts(read(paper, "venue.md", created))
    return [f"venue.md {part}: not done: {item}" for item in unchecked(venue.get(part, ""))]


def plan_cards(paper: Path) -> list[dict]:
    """Every plan card with its section and approval, for the status view."""
    return [{"file": p.name, "section": c["Section"], "approved": plans.approved(c), "problems": plans.problems(c)}
            for p in sorted((folder(paper) / "plans").glob("*.md")) for c in [plans.parse(p.read_text(encoding="utf-8"))]]
