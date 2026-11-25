"""The texts the paper is compared against. Plain code, no model calls.

Three kinds: own (the author's earlier papers, read from files), reference (each
bibliography entry that resolves to an arXiv paper: its full text from arXiv HTML or
ar5iv, else its abstract; an entry not on arXiv: a PDF the author has in workflow/papers/
or Zotero storage, else its open-access PDF, see library.py) and search (abstracts of the top arXiv and Semantic Scholar
results for the paper's title). Every source is {id, kind, title, url, file, level,
words}; words are the compared words and never leave this package's matching.
"""

import re
from pathlib import Path

from auto_research.tasks.citations import sources as net
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.library import resolve
from auto_research.tasks.survey import fulltext, search

from . import library
from .prose import norm_words, tex_words

OWN_SUFFIXES = (".tex", ".pdf", ".txt", ".md")
MAX_WORKS = 30          # bibliography works compared at most
SEARCH_TOP = 10         # search results compared


def source(sid, kind, title, words, url="", file="", level="full") -> dict:
    return {"id": sid, "kind": kind, "title": title, "url": url, "file": file, "level": level, "words": words}


# Own earlier papers

def own_files(paper: Path, own_papers: str) -> list[Path]:
    """Files named in own_papers (comma-separated files or folders, relative to the
    paper or absolute) plus every file under workflow/own_papers/."""
    roots = []
    for part in (p.strip() for p in (own_papers or "").split(",")):
        if not part:
            continue
        path = Path(part).expanduser()
        path = path if path.is_absolute() else paper / path
        if not path.exists():
            raise ValueError(f"own paper {part} does not exist")
        roots.append(path)
    folder = paper / "workflow" / "own_papers"
    if folder.is_dir():
        roots.append(folder)
    files = []
    for root in roots:
        found = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        files += [p.resolve() for p in found if p.suffix.lower() in OWN_SUFFIXES and p.resolve() not in files]
    return files


def read_own(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".tex":
        return tex_words(path)
    if suffix == ".pdf":
        from pypdf import PdfReader
        return norm_words("\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))
    return norm_words(path.read_text(encoding="utf-8", errors="ignore"))


def own_sources(paper: Path, own_papers: str, unavailable: list[str]) -> list[dict]:
    out = []
    for path in own_files(paper, own_papers):
        try:
            words = read_own(path)
        except Exception as e:  # noqa: BLE001  an unreadable PDF is named, the run goes on
            unavailable.append(f"own paper {path.name}: {type(e).__name__}")
            continue
        if not words:
            unavailable.append(f"own paper {path.name}: no text")
            continue
        shown = str(path.relative_to(paper)) if path.is_relative_to(paper) else str(path)
        out.append(source(f"own:{shown}", "own", path.stem, words, file=shown, level="text"))
    return out


# Bibliography works on arXiv

def arxiv_id(ref: dict) -> str:
    if ref.get("arxiv"):
        return re.sub(r"v\d+$", "", ref["arxiv"])
    doi = resolve.clean_doi(ref.get("doi") or "")
    return doi.split("arxiv.", 1)[1] if doi.startswith("10.48550/arxiv.") else ""


def bib_arxiv(paper: Path) -> list[tuple[dict, str, dict | None]]:
    """(entry, arXiv id, arXiv record or None) for the first MAX_WORKS entries that
    resolve to arXiv: by eprint or arXiv DOI, else by an arXiv title search whose best
    hit matches the title closely."""
    refs = resolve.entries(paper)
    known = [(r, arxiv_id(r)) for r in refs if arxiv_id(r)][:MAX_WORKS]
    records = {c["arxiv"]: c for c in search.arxiv_ids([i for _, i in known])} if known else {}
    out = [(r, i, records.get(i)) for r, i in known]
    for r in refs:
        if len(out) >= MAX_WORKS:
            break
        if arxiv_id(r) or not r.get("title"):
            continue
        hits = search.arxiv_search(r["title"], 3)
        best = max(hits, key=lambda c: title_sim(r["title"], c["title"]), default=None)
        if best and title_sim(r["title"], best["title"]) >= AUTHENTIC_TITLE:
            out.append((r, best["arxiv"], best))
    return out


def full_text(arxiv: str, cache: Path) -> str:
    """The body of one arXiv paper from its HTML version, cached as plain text."""
    path = cache / (arxiv.replace("/", "_") + ".txt")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    for pattern in fulltext.SOURCES:
        html = net.fetch(pattern.format(id=arxiv), as_json=False, tries=2)
        found = fulltext.sections(html) if html else []
        if len(found) >= 2:
            text = "\n\n".join(s["text"] for s in found)
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return text
    return ""


def reference_sources(paper: Path, cache: Path, unavailable: list[str]) -> list[dict]:
    out = []
    for ref, arxiv, record in bib_arxiv(paper):
        abstract = (record or {}).get("abstract") or ""
        body = full_text(arxiv, cache)
        words = norm_words(abstract + "\n" + body)
        if not words:
            unavailable.append(f"arXiv:{arxiv} ({ref['key']}): no text")
            continue
        out.append(source(f"arXiv:{arxiv}", "reference", (record or {}).get("title") or ref["title"],
                          words, url=f"https://arxiv.org/abs/{arxiv}", file=ref["key"],
                          level="full" if body else "abstract"))
    return out


# Search on the paper's title

def search_sources(title: str, skip: set[str]) -> list[dict]:
    """Abstracts of the top results for the title; the paper itself (same title) and
    works already compared (skip, by arXiv id) are left out."""
    if not title:
        return []
    found = search.merge([search.arxiv_search(title, SEARCH_TOP), search.s2_search(title, SEARCH_TOP)])
    out = []
    for c in found:
        if len(out) >= SEARCH_TOP:
            break
        if not c["abstract"] or (c["arxiv"] and f"arXiv:{c['arxiv']}" in skip) or \
                title_sim(title, c["title"]) >= AUTHENTIC_TITLE:
            continue
        out.append(source(c["id"], "search", c["title"], norm_words(c["abstract"]), url=c["url"], level="abstract"))
    return out


def library_sources(paper: Path, done: set[str], cache: Path, unavailable: list[str], room: int) -> list[dict]:
    """Full text of bibliography works not on arXiv: a PDF the author has (workflow/papers/,
    Zotero storage) or the open-access copy OpenAlex records."""
    if room <= 0:
        return []
    files = library.index(paper, cache)
    out = []
    for ref in resolve.entries(paper):
        if len(out) >= room:
            break
        if ref["key"] in done or arxiv_id(ref) or not (ref.get("title") or ref.get("doi")):
            continue
        text, where, link = library.library_text(paper, ref, files, cache)
        if not text:
            continue
        words = norm_words(text)
        if not words:
            continue
        is_url = link.startswith("http")
        sid = f"doi:{ref['doi']}" if ref.get("doi") else f"bib:{ref['key']}"
        out.append(source(sid, "reference", ref.get("title") or ref["key"], words,
                          url=link if is_url else (f"https://doi.org/{ref['doi']}" if ref.get("doi") else ""),
                          file=ref["key"], level="local" if where == "local" else "open_access"))
    return out


def gather(paper: Path, own_papers: str, title: str, cache: Path) -> tuple[list[dict], list[str]]:
    """Every source and the sources or look-ups that could not be read."""
    unavailable: list[str] = []
    net.UNAVAILABLE.clear()
    own = own_sources(paper, own_papers, unavailable)
    refs = reference_sources(paper, cache, unavailable)
    refs += library_sources(paper, {s["file"] for s in refs}, cache, unavailable, MAX_WORKS - len(refs))
    found = search_sources(title, {s["id"] for s in refs})
    return own + refs + found, sorted(net.UNAVAILABLE) + unavailable


__all__ = ["gather", "own_files"]
