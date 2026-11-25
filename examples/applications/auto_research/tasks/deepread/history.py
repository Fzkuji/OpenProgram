"""Mark each reference of a work by the author's own history: cited by you (an entry of
the project's .bib), read by you (a work the project's reading files name), or new.
Plain code, no model calls.

What counts as read: the .bib keys of workflow/annotations.csv (import_annotations),
workflow/literature.md, workflow/literature_table.csv and workflow/claim_support.csv;
the works kept (core, adjacent, survey) by every field survey under
workflow/surveys/*/*/survey.json (research_survey); and the rows of workflow/watch.csv
the author marked handled. A reference matches a known work by arXiv id, DOI, or a
title similar enough (citations.refs.title_sim at AUTHENTIC_TITLE).
"""

import csv
import io
import json
import re
from pathlib import Path

from auto_research import stages
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.library import resolve
from auto_research.tasks.watch.workflow import load as watch_rows

STATUS = ("new", "read", "cited")
LITERATURE_KEY = re.compile(r"^-\s+([^\s:]+)\s*:", re.M)


def ident(rec: dict) -> dict:
    return {"title": rec.get("title") or "", "arxiv": re.sub(r"v\d+$", "", (rec.get("arxiv") or "").strip()),
            "doi": resolve.clean_doi(rec.get("doi") or "")}


def same(a: dict, b: dict) -> bool:
    if a["arxiv"] and a["arxiv"] == b["arxiv"]:
        return True
    if a["doi"] and a["doi"] == b["doi"]:
        return True
    return bool(a["title"] and b["title"]) and title_sim(a["title"], b["title"]) >= AUTHENTIC_TITLE


def csv_column(path: Path, column: str) -> list[str]:
    if not path.is_file():
        return []
    with io.StringIO(path.read_text(encoding="utf-8", errors="ignore"), newline="") as f:
        return [(r.get(column) or "").strip() for r in csv.DictReader(f) if (r.get(column) or "").strip()]


def known_works(paper: Path) -> tuple[list[dict], list[dict]]:
    """(the project's .bib entries, the works it has read with where each was read)."""
    bib = [dict(ident(e), key=e["key"]) for e in resolve.entries(paper)]
    by_key = {b["key"]: b for b in bib}
    folder = stages.folder(paper)
    read = []
    keyed = {"annotations.csv": csv_column(folder / "annotations.csv", "key"),
             "literature_table.csv": csv_column(folder / "literature_table.csv", "key"),
             "claim_support.csv": csv_column(folder / "claim_support.csv", "work")}
    lit = folder / "literature.md"
    keyed["literature.md"] = LITERATURE_KEY.findall(lit.read_text(encoding="utf-8", errors="ignore")) if lit.is_file() else []
    for where, keys in keyed.items():
        for k in dict.fromkeys(keys):
            if k in by_key:
                read.append(dict(by_key[k], where=where))
    for path in sorted(folder.glob("surveys/*/*/survey.json")):
        try:
            survey = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for p in survey.get("papers") or []:
            if isinstance(p, dict) and p.get("decision") in ("core", "adjacent", "survey"):
                read.append(dict(ident(p), where=f"survey {survey.get('direction') or path.parent.parent.name}"))
    for r in watch_rows(paper):
        if r.get("handled"):
            arxiv = re.sub(r"^arxiv:", "", r.get("id", ""), flags=re.I)
            read.append(dict(ident({"title": r.get("title"), "arxiv": arxiv if re.match(r"\d{4}\.\d{4,5}", arxiv) else "",
                                    "doi": r.get("id", "")[4:] if r.get("id", "").startswith("doi:") else ""}),
                             where="watch.csv"))
    return bib, read


def mark(references: list[dict], paper: Path) -> list[dict]:
    """Each reference with status (cited, read, new), the matching .bib key and where it was read."""
    bib, read = known_works(paper)
    out = []
    for ref in references:
        me = ident(ref)
        cited = next((b["key"] for b in bib if same(me, b)), "")
        where = list(dict.fromkeys(r["where"] for r in read if same(me, r)))
        status = "cited" if cited else "read" if where else "new"
        out.append(dict(ref, status=status, bib_key=cited, read_in=where))
    return out


def ordered(references: list[dict]) -> list[dict]:
    """New references first, most cited inside the work first; then read, then cited."""
    return sorted(references, key=lambda r: (STATUS.index(r["status"]), -r["count"], r["n"]))


__all__ = ["known_works", "mark", "ordered"]
