"""The candidate works of a meta-analysis, their titles and abstracts for screening, and
their full text for extraction. Plain code, no model calls.

Candidates: `works` (arXiv ids, DOIs or PDF paths, comma-separated, or a .bib file),
else the project's literature: every .bib entry (library.resolve.entries) and the core
and adjacent papers of the field surveys under workflow/surveys/ (research_survey).
Titles and abstracts come from the arXiv API (survey.search.arxiv_ids) and OpenAlex by
DOI. Full text, in this order: the arXiv HTML with its tables (reading.reader.work_text),
the arXiv LaTeX source or PDF (deepread.source.get_work), and for a work without arXiv
the author's PDFs or an open-access copy (overlap.library.library_text). Every network
call goes through citations.sources.fetch or deepread.source.fetch_bytes.
"""

import json
import re
from pathlib import Path

from auto_research import stages
from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import parse_bib
from auto_research.tasks.deepread import source as deep
from auto_research.tasks.deepread.history import ident, same
from auto_research.tasks.library import resolve
from auto_research.tasks.overlap import library
from auto_research.tasks.reading import reader
from auto_research.tasks.survey import search

MAX_WORKS = 60
TEX_CHARS = 90_000          # of a LaTeX source or PDF text handed to the extractor
KEPT = ("core", "adjacent")


def work(title="", arxiv="", doi="", key="", abstract="", year=None, pdf="", origin="") -> dict:
    arxiv = re.sub(r"v\d+$", "", (arxiv or "").strip())
    doi = resolve.clean_doi(doi or "")
    if not arxiv and doi.startswith("10.48550/arxiv."):
        arxiv, doi = doi.split("arxiv.", 1)[1], ""
    wid = f"arXiv:{arxiv}" if arxiv else f"doi:{doi}" if doi else key or Path(pdf).name or title[:40]
    return {"id": wid, "key": key, "title": " ".join((title or "").split()), "abstract": " ".join((abstract or "").split()),
            "arxiv": arxiv, "doi": doi, "year": year, "pdf": pdf, "origin": origin}


def from_bib(path: Path, origin: str) -> list[dict]:
    return [work(r["title"], r["arxiv"], r["doi"], r["key"], year=r["year"], origin=origin)
            for r in parse_bib(path.read_text(encoding="utf-8", errors="ignore"))]


def inside(paper: Path, name: str) -> Path:
    path = Path(name).expanduser()
    return (path if path.is_absolute() else paper / path).resolve()


def candidates(paper: Path, works: str) -> tuple[list[dict], str]:
    """(candidate works, where they came from). ValueError for an id that is not an arXiv
    id, a DOI or an existing PDF, or a .bib that does not exist."""
    text = str(works or "").strip()
    if text.lower().endswith(".bib") and "," not in text:
        path = inside(paper, text)
        if not path.is_file():
            raise ValueError(f"the .bib file {text} does not exist")
        found, origin = from_bib(path, path.name), path.name
    elif text:
        found, origin = [], "works"
        for item in (x.strip() for x in text.split(",")):
            if not item:
                continue
            kind, value = deep.parse_work(item)
            found.append(work(arxiv=value if kind == "arxiv" else "", doi=value if kind == "doi" else "",
                              pdf=value if kind == "pdf" else "", origin="works"))
    else:
        found = [work(e["title"], e["arxiv"], e["doi"], e["key"], year=e["year"], origin=e.get("file", ".bib"))
                 for e in resolve.entries(paper)]
        for path in sorted(stages.folder(paper).glob("surveys/*/*/survey.json")):
            try:
                survey = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for p in survey.get("papers") or []:
                if isinstance(p, dict) and p.get("decision") in KEPT:
                    found.append(work(p.get("title"), p.get("arxiv"), p.get("doi"), abstract=p.get("abstract"),
                                      year=p.get("year"), origin=f"survey {survey.get('direction') or path.parent.parent.name}"))
        origin = "project"
    out = []
    for c in found:
        if not any(same(ident(c), ident(o)) for o in out):
            out.append(c)
    return out[:MAX_WORKS], origin


def openalex_doi(doi: str) -> dict:
    w = sources.fetch(f"https://api.openalex.org/works/doi:{doi}",
                      {"select": "id,title,publication_year,abstract_inverted_index,locations",
                       **sources._env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex") or {}
    urls = [loc.get("landing_page_url") or "" for loc in w.get("locations") or []]
    arxiv = next((m.group(1) for u in urls if (m := search.ARXIV_ID.search(u))), "")
    return {"title": w.get("title") or "", "abstract": search.abstract_text(w.get("abstract_inverted_index")),
            "year": w.get("publication_year"), "arxiv": arxiv}


def describe(found: list[dict], notes: list[str]) -> None:
    """Fill each candidate's title, abstract, year and arXiv id in place."""
    for c in found:
        if not c["arxiv"] and not c["doi"] and not c["pdf"] and c["title"]:
            arxiv, _ = reader.arxiv_of({"title": c["title"], "doi": "", "arxiv": ""})
            c["arxiv"] = arxiv
        if c["doi"] and (not c["abstract"] or not c["arxiv"]):
            meta = openalex_doi(c["doi"])
            c["arxiv"] = c["arxiv"] or meta["arxiv"]
            c["title"] = c["title"] or meta["title"]
            c["abstract"] = c["abstract"] or meta["abstract"]
            c["year"] = c["year"] or meta["year"]
    need = [c["arxiv"] for c in found if c["arxiv"] and not c["abstract"]]
    if need:
        got = {r["arxiv"]: r for r in search.arxiv_ids(need)}
        for c in found:
            r = got.get(c["arxiv"])
            if r and not c["abstract"]:
                c["title"] = c["title"] or r["title"]
                c["abstract"] = r["abstract"]
                c["year"] = c["year"] or r["year"]
    for c in found:
        if c["pdf"] and not c["title"]:
            try:
                first = deep.pdf_text(Path(c["pdf"]))[:3000]
            except ValueError:
                first = ""
            c["title"] = " ".join(first.split("\n", 1)[0].split()) or Path(c["pdf"]).stem
            c["abstract"] = " ".join(first.split())[:2000]
        if not c["title"]:
            notes.append(f"{c['id']}: no title or abstract could be found; screened by its id only")
            c["title"] = c["id"]


def full_text(c: dict, paper: Path, cache: Path, files: list[dict] | None, notes: list[str]) -> tuple[str, str, str]:
    """(text, where, url) of one included work; ("", "", "") when nothing could be read."""
    if c["arxiv"]:
        text, url = reader.work_text(c["arxiv"])
        if text:
            return text, "arXiv HTML", url
        got = deep.get_work(c["arxiv"], cache / "sources" / re.sub(r"[^\w.]+", "_", c["arxiv"]), notes)
        if got.get("kind") == "latex":
            return got["tex"][:TEX_CHARS], "arXiv LaTeX", got["url"]
        if got.get("kind") == "pdf":
            return got["text"][:TEX_CHARS], "arXiv PDF", got["url"]
        return "", "", ""
    if c["pdf"]:
        try:
            return deep.pdf_text(Path(c["pdf"]))[:TEX_CHARS], "PDF", c["pdf"]
        except ValueError as e:
            notes.append(f"{c['id']}: {e}")
            return "", "", ""
    ref = {"key": c["key"], "doi": c["doi"], "title": c["title"]}
    text, where, url = library.library_text(paper, ref, files or [], cache)
    return text[:TEX_CHARS], where, url


__all__ = ["candidates", "describe", "full_text"]
