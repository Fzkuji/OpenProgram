"""Find the paper's bibliographies, resolve each entry to an OpenAlex work, and read
Crossref's retraction and correction notices. Plain code, no model calls.

Every request goes through citations.sources.fetch, so a source that fails is named
in UNAVAILABLE and tests replace fetch alone.
"""

import re
import urllib.parse
from pathlib import Path

from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, parse_bib, title_sim
from auto_research.tasks.revision.rules import paper as paper_rules

OA_SELECT = "id,doi,title,publication_year,cited_by_count,is_retracted,referenced_works,primary_location"

# Crossref update types, as the notice kinds this task reports.
KINDS = {"retraction": "retraction", "retracted": "retraction", "removal": "retraction",
         "withdrawal": "retraction", "partial_retraction": "retraction",
         "correction": "correction", "corrigendum": "correction", "erratum": "erratum",
         "expression_of_concern": "expression_of_concern"}


def bib_files(paper: Path) -> list[Path]:
    """The bibliographies the paper's rules name, when they exist; otherwise every
    *.bib in the repository root. drafts/ is never read."""
    named = [paper / n for n in paper_rules.load(paper).bib_files if (paper / n).is_file()]
    return named or sorted(p for p in paper.glob("*.bib") if p.is_file())


def entries(paper: Path) -> list[dict]:
    """Every entry of every bibliography, once per key."""
    out, seen = [], set()
    for bib in bib_files(paper):
        for ref in parse_bib(bib.read_text(encoding="utf-8", errors="ignore")):
            if ref["key"] not in seen:
                seen.add(ref["key"])
                out.append(dict(ref, doi=clean_doi(ref["doi"]), file=bib.name))
    return out


def clean_doi(doi: str) -> str:
    doi = (doi or "").strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi)


def short(work_id: str) -> str:
    return (work_id or "").rsplit("/", 1)[-1]


def _work(path: str) -> dict | None:
    d = sources.fetch("https://api.openalex.org/works/" + path,
                      {"select": OA_SELECT, **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return d if isinstance(d, dict) and d.get("id") else None


def resolve(ref: dict) -> tuple[dict | None, str]:
    """The OpenAlex work of one entry and how it was found: by DOI, else arXiv id,
    else a title search whose best hit must match the title closely."""
    if ref["doi"]:
        w = _work("doi:" + ref["doi"])
        if w:
            return w, "doi"
    if ref["arxiv"]:
        w = _work("doi:10.48550/arxiv." + ref["arxiv"])
        if w:
            return w, "arxiv"
    if ref["title"]:
        d = sources.fetch("https://api.openalex.org/works",
                          {"search": ref["title"], "per-page": 5, "select": OA_SELECT,
                           **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
        hits = [w for w in (d or {}).get("results") or [] if w.get("id")]
        best = max(hits, key=lambda w: title_sim(ref["title"], w.get("title") or ""), default=None)
        if best and title_sim(ref["title"], best.get("title") or "") >= AUTHENTIC_TITLE:
            return best, "title"
    return None, ""


def work_doi(work: dict | None) -> str:
    return clean_doi((work or {}).get("doi") or "")


def notices(doi: str) -> list[dict]:
    """Crossref's update relations of one DOI. updated-by: a notice about this work.
    update-to: this DOI is itself a notice about another work."""
    if not doi or doi.startswith("10.48550/"):
        return []
    d = sources.fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi),
                      sources._env("CROSSREF_MAILTO", "mailto"), label="Crossref")
    message = (d or {}).get("message") or {}
    out = []
    for relation in ("updated-by", "update-to"):
        for u in message.get(relation) or []:
            kind = KINDS.get(str(u.get("type") or "").lower().replace("-", "_").replace(" ", "_"))
            if kind:
                notice = clean_doi(u.get("DOI") or "")
                out.append({"kind": kind, "relation": relation, "notice_doi": notice,
                            "notice_url": f"https://doi.org/{notice}" if notice else ""})
    return out


__all__ = ["bib_files", "clean_doi", "entries", "notices", "resolve", "short", "work_doi"]
