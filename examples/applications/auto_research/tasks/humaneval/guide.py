"""The rating guide and the system outputs it is applied to. Plain code, no model calls.

workflow/eval_guide.md is the author's file: the mode (pairwise or rating), the scale,
the instructions raters read, and the criteria, each with the question raters answer,
guidance, and the claim it tests. It is drafted once (by the guide_writer role, or from
`criteria`) and kept as the author edits it; `Approved: yes` marks it ready to publish.
"""

import csv
import io
import json
import re
from pathlib import Path

from auto_research import stages

GUIDE = "eval_guide.md"           # in workflow/
MODES = ("pairwise", "rating")
SCALE = 5
MIN_SCALE, MAX_SCALE = 3, 7
ITEM = ("item", "item_id", "id", "prompt_id", "example_id")
PROMPT = ("prompt", "input", "source", "question", "instruction")
SYSTEM = ("system", "model", "config", "configuration")
OUTPUT = ("output", "response", "completion", "summary", "answer", "text")
MODE_PREFIX = re.compile(r"^\s*(pairwise|rating)(?:\s+1\s*[-–]\s*(\d+))?\s*:\s*", re.I)
FIELD = re.compile(r"^([A-Z][A-Za-z ]*):[ \t]*(.*)$", re.M)
DEFAULT_INSTRUCTIONS = ("You will see an input and the responses of AI systems to it. Read the input and the "
                        "responses carefully, then answer each question about them. There are no right answers "
                        "except on attention checks; judge by the guidance given.")


# System outputs -------------------------------------------------------------------------------

def resolve(paper: Path, name: str) -> Path:
    p = Path(name).expanduser()
    return p if p.is_absolute() else paper / p


def pick(row: dict, names: tuple) -> str:
    low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for n in names:
        if n in low and low[n] is not None and str(low[n]).strip():
            return str(low[n]).strip()
    return ""


def read_outputs(paper: Path, outputs: str) -> tuple[list[dict], list[str]]:
    """Rows {item, prompt, system, output} of a JSONL, CSV or TSV file; a repeated
    (item, system) keeps its first row. ValueError for a missing file, an unknown format
    or no usable row."""
    if not str(outputs or "").strip():
        raise ValueError("outputs is required: a JSONL or CSV file of item, prompt, system, output")
    path = resolve(paper, outputs.strip())
    if not path.is_file():
        raise ValueError(f"{path} does not exist")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".jsonl", ".json"):
        raw = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    raise ValueError(f"{path.name} line {n} is not JSON") from None
    elif path.suffix.lower() in (".csv", ".tsv"):
        raw = list(csv.DictReader(io.StringIO(text), delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
    else:
        raise ValueError(f"{path.name}: outputs must be .jsonl, .csv or .tsv")
    rows, seen, notes = [], set(), []
    for r in raw:
        if not isinstance(r, dict):
            continue
        row = {"item": pick(r, ITEM), "prompt": pick(r, PROMPT), "system": pick(r, SYSTEM), "output": pick(r, OUTPUT)}
        if not (row["item"] and row["system"] and row["output"]):
            continue
        if (row["item"], row["system"]) in seen:
            notes.append(f"条目 {row['item']} 有不止一条 {row['system']} 的输出，只用第一条")
            continue
        seen.add((row["item"], row["system"]))
        rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} has no row with an item id, a system and an output "
                         f"(columns such as {ITEM[0]}, {PROMPT[0]}, {SYSTEM[0]}, {OUTPUT[0]})")
    return rows, notes


def systems_of(rows: list[dict]) -> list[str]:
    return sorted({r["system"] for r in rows})


def paired_items(rows: list[dict]) -> int:
    per: dict[str, set] = {}
    for r in rows:
        per.setdefault(r["item"], set()).add(r["system"])
    return sum(len(s) >= 2 for s in per.values())


# The guide ----------------------------------------------------------------------------------

def slug(name: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "criterion"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    taken.add(out)
    return out


def with_ids(criteria: list[dict]) -> list[dict]:
    taken: set = set()
    return [c | {"id": slug(c["name"], taken)} for c in criteria]


def from_criteria(criteria: str, can_pair: bool) -> dict:
    """The guide the author typed as `criteria`: "[pairwise: | rating 1-7:] name [= question]; ..."."""
    text = criteria.strip()
    m = MODE_PREFIX.match(text)
    mode = (m.group(1).lower() if m else "pairwise" if can_pair else "rating")
    scale = int(m.group(2)) if m and m.group(2) else SCALE
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise ValueError(f"criteria: the rating scale must run from 1 to {MIN_SCALE}..{MAX_SCALE}")
    body = text[m.end():] if m else text
    parts = [p.strip() for p in re.split(r";" if ";" in body else r",", body) if p.strip()]
    if not parts:
        raise ValueError("criteria names no criterion")
    out = []
    for p in parts:
        name, _, question = (x.strip() for x in p.partition("="))
        if not question:
            question = (f"Which response is better in {name.lower()}?" if mode == "pairwise"
                        else f"How good is the response in {name.lower()}?")
        out.append({"name": name[:40], "question": question, "guidance": "", "claim": ""})
    return {"approved": True, "mode": mode, "scale": scale, "instructions": DEFAULT_INSTRUCTIONS,
            "criteria": with_ids(out), "source": "criteria"}


def parse(text: str) -> dict:
    """workflow/eval_guide.md as a guide."""
    head = text.split("\n## ", 1)[0]
    fields = {k: v.strip() for k, v in FIELD.findall(head)}
    mode = fields.get("Mode", "pairwise").lower()
    try:
        scale = int(fields.get("Scale", SCALE) or SCALE)
    except ValueError:
        scale = SCALE
    instructions, criteria = DEFAULT_INSTRUCTIONS, []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, rest = block.partition("\n")
        title = title.strip()
        if title.lower() == "instructions":
            instructions = rest.strip() or DEFAULT_INSTRUCTIONS
        elif title.lower().startswith("criterion:"):
            f = {k: v.strip() for k, v in FIELD.findall(rest)}
            name = title.split(":", 1)[1].strip()
            if name and f.get("Question"):
                criteria.append({"name": name[:40], "question": f["Question"], "guidance": f.get("Guidance", ""),
                                 "claim": f.get("Claim", "")})
    return {"approved": fields.get("Approved", "").lower() in ("yes", "y", "true"),
            "mode": mode if mode in MODES else "pairwise", "scale": min(max(scale, MIN_SCALE), MAX_SCALE),
            "instructions": instructions, "criteria": with_ids(criteria), "source": f"workflow/{GUIDE}"}


def render(guide: dict) -> str:
    lines = ["# Human evaluation guide", "",
             "Edit freely; set Approved to yes before publishing the survey. Mode is pairwise or rating;",
             "Scale is the top of the 1..N rating scale. Raters read the Instructions and, for each",
             "criterion, its Question and Guidance. Claim names the claim of claims.md it tests.", "",
             f"Approved: {'yes' if guide['approved'] else 'no'}", f"Mode: {guide['mode']}",
             f"Scale: {guide['scale']}", "", "## Instructions", "", guide["instructions"].strip(), ""]
    for c in guide["criteria"]:
        lines += [f"## Criterion: {c['name']}", "", f"Question: {c['question']}", f"Guidance: {c['guidance']}",
                  f"Claim: {c['claim']}", ""]
    return "\n".join(lines)


def path(paper: Path) -> Path:
    return stages.folder(paper) / GUIDE


def claims(paper: Path) -> dict[str, str]:
    p = stages.folder(paper) / "claims.md"
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    if "C1: The claim, stated as a result" in text:       # the untouched template
        return {}
    return dict(stages.CLAIM.findall(text))


def mentions(text: str, system: str) -> bool:
    """Whether a system name occurs in text as a word (case-insensitive)."""
    return bool(re.search(rf"(?<![\w-]){re.escape(system)}(?![\w-])", text, re.I))


def redact(guide: dict, systems: list[str]) -> list[str]:
    """Remove system names from what raters read (the survey is blind); returns the names found."""
    found = []
    for s in systems:
        if len(s) < 3:
            continue
        pat = re.compile(rf"(?<![\w-]){re.escape(s)}(?![\w-])", re.I)
        for c in [guide] + guide["criteria"]:
            for k in ("instructions", "question", "guidance"):
                if k in c and pat.search(c[k]):
                    c[k] = pat.sub("[hidden]", c[k])
                    found.append(s)
    return sorted(set(found))


__all__ = ["DEFAULT_INSTRUCTIONS", "GUIDE", "claims", "from_criteria", "mentions", "parse", "path", "read_outputs",
           "redact", "render", "resolve", "systems_of"]
