"""The project's argument map and the findings of other projects. Plain code, no model.

Nodes: the claims of workflow/claims.md (C ids), the evidence of evidence.csv (E ids),
the rows of experiment_plan.csv (X1, X2, ... in row order), the findings of findings.md
(F ids) and the section plan cards of plans/*.md (S:<file stem>). Edges:
evidence supports claim, experiment planned_for claim, claim and evidence argued_in
section, finding supports or refutes the claims its hypothesis names.
Template rows and placeholders are not work and are left out.
"""

import re
import subprocess
from pathlib import Path

from auto_research import checks, stages
from auto_research.tasks.revision.steps.prepare import plans

PLACEHOLDER_CLAIM = "The claim, stated as a result"
OPEN_STATUS = ("planned", "running")
ID = re.compile(r"\b([CEF]\d+)\b")


def _text(paper: Path, name: str) -> str:
    file = stages.folder(paper) / name
    return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""


def read_claims(paper: Path) -> dict[str, str]:
    return {cid: text.strip() for cid, text in stages.CLAIM.findall(_text(paper, "claims.md"))
            if not text.startswith(PLACEHOLDER_CLAIM)}


def read_rows(paper: Path, name: str, columns: tuple[str, ...]) -> list[dict]:
    text = _text(paper, name)
    if not text.strip():
        return []
    problems, rows = checks.table(text, columns, name)
    return [] if problems else checks.own_rows(rows, name)


def read_findings(text: str) -> list[dict]:
    """The entries of a findings.md: `## F1: title` with Hypothesis, Run, Result, Verdict."""
    template = stages.template("findings.md")
    if not text.strip() or text.strip() == template.strip():
        return []
    skip = {e.strip() for e in re.split(r"^##\s+", template, flags=re.M)[1:]}
    out = []
    for entry in re.split(r"^##\s+", text, flags=re.M)[1:]:
        if entry.strip() in skip:
            continue
        head = entry.split("\n", 1)[0].strip()
        m = re.match(r"(F\d+)\s*[:：]\s*(.*)$", head)
        fid, title = (m.group(1), m.group(2).strip()) if m else (f"F{len(out) + 1}", head)
        values = checks.fields(entry)
        out.append({"id": fid, "title": title, "hypothesis": values.get("Hypothesis", ""),
                    "run": values.get("Run", ""), "result": values.get("Result", ""),
                    "verdict": values.get("Verdict", "").lower()})
    return out


def argument_map(paper) -> dict:
    """The argument map of the project at paper_dir: nodes, edges and gaps, from the
    files under workflow/ alone."""
    paper = Path(paper).expanduser().resolve()
    claims = read_claims(paper)
    evidence = read_rows(paper, "evidence.csv", stages.EVIDENCE_COLUMNS)
    experiments = read_rows(paper, "experiment_plan.csv", checks.PLAN_COLUMNS)
    findings = read_findings(_text(paper, "findings.md"))
    cards = [(p, plans.parse(p.read_text(encoding="utf-8"))) for p in sorted((stages.folder(paper) / "plans").glob("*.md"))]

    nodes, edges = [], []
    for cid, text in claims.items():
        nodes.append({"id": cid, "kind": "claim", "label": text})
    for row in evidence:
        nodes.append({"id": row["id"], "kind": "evidence", "label": row["statement"], "claim": row["claim"],
                      "source": row["source"]})
        if row["claim"] in claims:
            edges.append({"source": row["id"], "target": row["claim"], "type": "supports"})
    for i, row in enumerate(experiments, 1):
        xid = f"X{i}"
        nodes.append({"id": xid, "kind": "experiment", "label": row["experiment"], "claim": row["claim"],
                      "status": row["status"], "experiment_kind": row["kind"], "evidence": row["evidence"]})
        if row["claim"] in claims:
            edges.append({"source": xid, "target": row["claim"], "type": "planned_for"})
    for f in findings:
        nodes.append({"id": f["id"], "kind": "finding", "label": f["title"], "verdict": f["verdict"],
                      "result": f["result"]})
        named = [c for c in dict.fromkeys(ID.findall(f["hypothesis"] + " " + f["title"])) if c in claims]
        kind = {"supported": "supports", "refuted": "refutes"}.get(f["verdict"])
        edges += [{"source": f["id"], "target": c, "type": kind} for c in named if kind]
    evidence_ids = {r["id"] for r in evidence}
    for path, card in cards:
        sid = f"S:{path.stem}"
        nodes.append({"id": sid, "kind": "section", "label": card["Section"] or path.stem, "file": path.name})
        edges += [{"source": c, "target": sid, "type": "argued_in"} for c in plans.ids(card["Claims"]) if c in claims]
        edges += [{"source": e, "target": sid, "type": "argued_in"} for e in plans.ids(card["Evidence"])
                  if e in evidence_ids]

    gaps = []
    covered = {r["claim"] for r in evidence}
    planned = {r["claim"] for r in experiments if r["status"] != "dropped"}
    argued = {e["source"] for e in edges if e["type"] == "argued_in"}
    for cid in claims:
        if cid not in covered:
            gaps.append({"kind": "claim_without_evidence", "id": cid, "unplanned": cid not in planned,
                         "text": f"{cid} 没有证据" + ("，也没有计划中的实验" if cid not in planned else "，但已有计划中的实验")})
    gaps += [{"kind": "unknown_claim", "id": r["id"], "text": f"证据 {r['id']} 指向不存在的主张 {r['claim'] or '（空）'}"}
             for r in evidence if r["claim"] not in claims]
    gaps += [{"kind": "claim_not_argued", "id": cid, "text": f"{cid} 没有在任何章节计划里论证"}
             for cid in claims if cid not in argued]
    gaps += [{"kind": "experiment_not_done", "id": f"X{i}",
              "text": f"X{i}（{row['claim']}）{row['experiment']} 还是 {row['status']}"}
             for i, row in enumerate(experiments, 1) if row["status"] in OPEN_STATUS]
    return {"nodes": nodes, "edges": edges, "gaps": gaps, "claims": claims, "findings": findings}


def summary(amap: dict) -> str:
    """The map as short text for the cartographer."""
    lines = ["Nodes:"]
    for n in amap["nodes"]:
        extra = "".join(f", {k} {n[k]}" for k in ("claim", "status", "verdict", "source") if n.get(k))
        lines.append(f"- {n['id']} ({n['kind']}{extra}): {n['label'][:200]}")
    lines.append("Edges:")
    lines += [f"- {e['source']} {e['type']} {e['target']}" for e in amap["edges"]] or ["- none"]
    lines.append("Gaps:")
    lines += [f"- {g['text']}" for g in amap["gaps"]] or ["- none"]
    return "\n".join(lines)


def known_projects() -> list[tuple[str, str]]:
    """(name, path) of the projects OpenProgram knows; none when it cannot be read."""
    try:
        from openprogram.state.store.project.project_store import list_projects
        return [(p.name, p.path) for p in list_projects() if getattr(p, "path", "")]
    except Exception:  # noqa: BLE001  not inside OpenProgram, or its store cannot be read
        return []


def shared_findings(paper, other_projects: str = "") -> list[dict]:
    """The findings of other projects: the repositories in other_projects (comma
    separated) and those OpenProgram knows; this project and folders that are not Git
    repositories are skipped."""
    paper = Path(paper).expanduser().resolve()
    named = [("", p.strip()) for p in other_projects.split(",") if p.strip()]
    seen, out = {paper}, []
    for name, raw in named + known_projects():
        repo = Path(raw).expanduser().resolve()
        if repo in seen or not (repo / ".git").exists():
            continue
        seen.add(repo)
        file = stages.folder(repo) / "findings.md"
        if not file.is_file():
            continue
        for f in read_findings(file.read_text(encoding="utf-8", errors="replace")):
            out.append(dict(f, project=name or repo.name, path=str(repo)))
    return out


def head(paper: Path) -> str:
    return subprocess.run(["git", "-C", str(paper), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


__all__ = ["argument_map", "head", "known_projects", "read_findings", "shared_findings", "summary"]
