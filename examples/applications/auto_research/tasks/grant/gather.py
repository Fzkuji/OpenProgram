"""What a grant proposal is written from: the project's brief, claims, findings, evidence,
best runs, the paper's abstract and introduction, and the literature with its BibTeX
keys. Plain code; nothing in the project is written.

`source` is every file read, joined: a number the proposal states must occur in it.
"""

import re
from pathlib import Path

from auto_research import brief, checks, stages

from ..proofread.workflow import abstract_and_intro, paper_title, split_sections
from ..revision.steps.prepare.source import body, flatten
from ..submission import common as sub

BEST_RUNS = 3
PAPER_CHARS = 12_000        # abstract plus introduction the roles see
ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.S)
TITLE = re.compile(r"\btitle\s*=\s*[{\"]((?:[^{}]|\{[^{}]*\})*)[}\"]", re.I | re.S)
YEAR = re.compile(r"\byear\s*=\s*[{\"]?(\d{4})", re.I)


def own_text(paper: Path, name: str) -> str:
    """The workflow file's text, or "" when it is missing or still its template."""
    path = stages.folder(paper) / name
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return "" if text == stages.template(name) else text


def claims(text: str) -> list[dict]:
    template = dict(stages.CLAIM.findall(stages.template("claims.md")))
    return [{"id": cid, "text": t.strip()} for cid, t in stages.CLAIM.findall(text) if template.get(cid) != t]


def findings(text: str) -> list[dict]:
    out = []
    for entry in re.split(r"^##\s+", text, flags=re.M)[1:]:
        head = entry.split("\n", 1)[0].strip()
        m = re.match(r"(F\d+)\s*:?\s*(.*)", head)
        fid, title = (m.group(1), m.group(2)) if m else (f"F{len(out) + 1}", head)
        values = checks.fields(entry)
        out.append({"id": fid, "title": title, **{k.lower(): values.get(k, "") for k in checks.FINDING_FIELDS}})
    return out


def evidence(text: str) -> list[dict]:
    if not text:
        return []
    problems, rows = checks.table(text, stages.EVIDENCE_COLUMNS, "evidence.csv")
    return [] if problems else [r for r in checks.own_rows(rows, "evidence.csv") if r.get("id")]


def best_runs(results: str, experiment: str) -> dict:
    """The best kept runs of results.tsv by the experiment's metric and direction."""
    spec = checks.fields(experiment) if experiment else {}
    lower = spec.get("Direction", "lower").lower() != "higher"
    if not results:
        return {"metric": spec.get("Metric", ""), "direction": "lower" if lower else "higher", "runs": []}
    problems, rows = checks.table(results, checks.RESULT_COLUMNS, "results.tsv", "\t")
    kept = [r for r in rows if not problems and r["status"] in ("keep", "merge") and checks.number(r["metric"]) is not None]
    kept.sort(key=lambda r: checks.number(r["metric"]), reverse=not lower)
    return {"metric": spec.get("Metric", ""), "direction": "lower" if lower else "higher",
            "runs": [{k: r[k] for k in ("commit", "metric", "description")} for r in kept[:BEST_RUNS]]}


def literature(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^-\s+([^\s:]+):\s*(.*)$", line)
        if m and m.group(1) != "key":
            out.append({"key": m.group(1), "note": m.group(2).strip()})
    return out


def bibliography(paper: Path) -> tuple[dict[str, dict], str]:
    """{key: {title, year}} of every top-level .bib file, and their joined text."""
    entries, texts = {}, []
    for bib in sorted(paper.glob("*.bib")):
        text = bib.read_text(encoding="utf-8", errors="ignore")
        texts.append(text)
        heads = list(ENTRY.finditer(text))
        for i, m in enumerate(heads):
            if m.group(1).lower() in ("comment", "string", "preamble"):
                continue
            chunk = text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            title, year = TITLE.search(chunk), YEAR.search(chunk)
            entries[m.group(2)] = {"title": " ".join(re.sub(r"[{}]", "", title.group(1)).split()) if title else "",
                                   "year": year.group(1) if year else ""}
    return entries, "\n".join(texts)


def paper_text(paper: Path) -> dict:
    """Title, abstract and introduction of the main .tex when there is one."""
    main = sub.main_tex(paper)
    if main is None:
        return {"file": "", "title": "", "abstract": "", "introduction": "", "full": ""}
    full = flatten(paper, main.relative_to(paper).as_posix())
    abstract, intro = abstract_and_intro(full, split_sections(body(full)))
    return {"file": main.name, "title": " ".join(paper_title(full).split()), "abstract": abstract,
            "introduction": intro[:PAPER_CHARS], "full": full}


def project(paper: Path) -> dict:
    """Everything the planner and the writer may use, and the source text that grounds numbers."""
    names = ("project.md", "claims.md", "findings.md", "evidence.csv", "results.tsv", "experiment.md", "literature.md")
    texts = {n: own_text(paper, n) for n in names}
    bib, bib_text = bibliography(paper)
    tex = paper_text(paper)
    found = {
        "brief": brief.read(paper),
        "claims": claims(texts["claims.md"]),
        "findings": findings(texts["findings.md"]),
        "evidence": evidence(texts["evidence.csv"]),
        "best_runs": best_runs(texts["results.tsv"], texts["experiment.md"]),
        "paper": {k: v for k, v in tex.items() if k != "full"},
        "literature": literature(texts["literature.md"]),
        "bibliography": bib,
    }
    found["read"] = [n for n, t in texts.items() if t] + ([tex["file"]] if tex["file"] else []) \
        + [b.name for b in sorted(paper.glob("*.bib"))]
    found["source"] = "\n".join([*texts.values(), tex["full"], bib_text])
    return found


def empty(found: dict) -> bool:
    return not (found["brief"]["claim"] or found["claims"] or found["findings"] or found["evidence"]
                or found["paper"]["abstract"] or found["paper"]["introduction"])


def ids(found: dict) -> dict[str, set[str]]:
    """The ids a role may point at: claims, and evidence rows with findings and best runs."""
    support = {e["id"] for e in found["evidence"]} | {f["id"] for f in found["findings"]}
    support |= {f"R{i}" for i in range(1, len(found["best_runs"]["runs"]) + 1)}
    return {"claims": {c["id"] for c in found["claims"]}, "support": support}


def context(found: dict) -> dict:
    """The project as the roles see it: best runs carry their ids R1, R2, ..."""
    runs = found["best_runs"]
    return {"brief": found["brief"], "claims": found["claims"], "findings": found["findings"],
            "evidence": found["evidence"],
            "best runs": {"metric": runs["metric"], "direction": runs["direction"],
                          "runs": [{"id": f"R{i}", **r} for i, r in enumerate(runs["runs"], 1)]},
            "paper": found["paper"], "literature": found["literature"]}


__all__ = ["context", "empty", "ids", "project"]
