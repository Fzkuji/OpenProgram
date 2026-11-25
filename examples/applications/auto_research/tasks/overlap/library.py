"""Full text of bibliography works that are not on arXiv. Plain code, no model calls.

Learned from Wiley's AI Gateway (subscribed full text handed to the agent) and Unpaywall
(the legal open-access copy of a DOI). Two places, in this order:

1. PDFs the author already has: workflow/papers/ and the Zotero storage folder
   (ZOTERO_STORAGE, else ~/Zotero/storage). Papers the author's institution subscribes
   to are read this way, from the copies the author downloaded. A PDF belongs to a work
   when its file name holds the citation key or the DOI, or its first page holds the
   work's title.
2. An open-access PDF from the locations OpenAlex records (built on Unpaywall's data),
   by DOI and by title (same-title records too): a location's PDF, else the
   citation_pdf_url of a landing page.

Every text read is cached under the run cache as plain text.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from auto_research.tasks.citations import sources as net
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim

MAX_INDEX = 400          # PDFs of the author's folders whose first page is read
FIRST_PAGE_CHARS = 1500


def folders(paper: Path) -> list[Path]:
    """workflow/papers/ and the Zotero storage folder, those that exist."""
    zotero = os.environ.get("ZOTERO_STORAGE") or str(Path.home() / "Zotero" / "storage")
    return [p for p in (paper / "workflow" / "papers", Path(zotero).expanduser()) if p.is_dir()]


def pdf_pages(path: Path, first_only: bool = False) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = reader.pages[:1] if first_only else reader.pages
    return "\n".join(page.extract_text() or "" for page in pages)


def index(paper: Path, cache: Path) -> list[dict]:
    """{path, name, first} for the PDFs of the author's folders, first pages cached by
    path and modification time."""
    store = cache / "library_index.json"
    try:
        known = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
    except ValueError:
        known = {}
    out, changed = [], False
    for root in folders(paper):
        for path in sorted(root.rglob("*.pdf"))[:MAX_INDEX]:
            key = f"{path}:{path.stat().st_mtime_ns}"
            if key not in known:
                try:
                    known[key] = pdf_pages(path, first_only=True)[:FIRST_PAGE_CHARS]
                except Exception:  # noqa: BLE001  an unreadable PDF is simply not matched
                    known[key] = ""
                changed = True
            out.append({"path": path, "name": path.name.lower(), "first": known[key]})
            if len(out) >= MAX_INDEX:
                break
    if changed:
        cache.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(known), encoding="utf-8")
    return out


def local_pdf(ref: dict, files: list[dict]) -> Path | None:
    key = (ref.get("key") or "").lower()
    doi = (ref.get("doi") or "").lower()
    doi_name = re.sub(r"[^a-z0-9]+", "_", doi) if doi else ""
    for f in files:
        name = re.sub(r"[^a-z0-9]+", "_", f["name"])
        if (key and len(key) >= 4 and key in f["name"]) or (doi_name and doi_name in name):
            return f["path"]
    title = ref.get("title") or ""
    if title:
        for f in files:
            lines = [ln.strip() for ln in f["first"].splitlines() if ln.strip()][:12]
            # a title may wrap over two lines of the first page
            candidates = lines + [a + " " + b for a, b in zip(lines, lines[1:])]
            if any(title_sim(title, c) >= AUTHENTIC_TITLE for c in candidates):
                return f["path"]
    return None


PDF_META = re.compile(r"""<meta[^>]+name=["']citation_pdf_url["'][^>]+content=["']([^"']+)["']""", re.I)


def open_access_urls(ref: dict) -> list[str]:
    """Candidate open-access PDFs of the work, best first: every location OpenAlex records
    for the work (and for other records with the same title, since a paper is often
    recorded twice): the best open-access PDF, any location's PDF, a landing page that is
    itself a PDF, then the citation_pdf_url a landing page declares (JMLR, PMLR,
    proceedings and repositories carry it)."""
    params = {"select": "id,title,best_oa_location,locations", **net._env("OPENALEX_API_KEY", "api_key")}
    works = []
    doi = ref.get("doi") or ""
    if doi:
        w = net.fetch(f"https://api.openalex.org/works/doi:{doi}", params, label="OpenAlex")
        works += [w] if w else []
    title = ref.get("title") or ""
    if title:
        found = net.fetch("https://api.openalex.org/works", {"search": title, "per-page": 5, **params},
                          label="OpenAlex") or {}
        works += [w for w in found.get("results") or [] if title_sim(title, w.get("title") or "") >= AUTHENTIC_TITLE]
    pdfs, pages = [], []
    for w in works:
        best = (w.get("best_oa_location") or {}).get("pdf_url")
        pdfs += [best] if best else []
        for loc in w.get("locations") or []:
            if loc.get("pdf_url"):
                pdfs.append(loc["pdf_url"])
            landing = loc.get("landing_page_url") or ""
            if landing.lower().split("?")[0].endswith(".pdf"):
                pdfs.append(landing)
            elif landing.startswith("http") and "doi.org/" not in landing:
                pages.append(landing)
    for page in pages[:4]:
        html = net.fetch(page, as_json=False, tries=1) or ""
        m = PDF_META.search(html)
        if m:
            pdfs.append(m.group(1))
    return list(dict.fromkeys(pdfs))


def download_text(url: str, cache: Path) -> str:
    from auto_research.tasks.deepread.source import fetch_bytes
    path = cache / ("oa_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    data = fetch_bytes(url, label="open-access PDF")
    if not data or data[:5] != b"%PDF-":
        return ""
    cache.mkdir(parents=True, exist_ok=True)
    pdf = cache / (path.stem + ".pdf")
    pdf.write_bytes(data)
    try:
        text = pdf_pages(pdf)
    except Exception:  # noqa: BLE001  a broken PDF gives no text
        text = ""
    finally:
        pdf.unlink(missing_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def library_text(paper: Path, ref: dict, files: list[dict], cache: Path) -> tuple[str, str, str]:
    """(text, where, url) of a non-arXiv work: where is "local" or "open access"; ("", "", "")
    when neither has it."""
    path = local_pdf(ref, files)
    if path is not None:
        try:
            text = pdf_pages(path)
        except Exception:  # noqa: BLE001
            text = ""
        if text.strip():
            shown = str(path.relative_to(paper)) if path.is_relative_to(paper) else path.name
            return text, "local", shown
    for url in open_access_urls(ref)[:4]:
        text = download_text(url, cache)
        if text.strip():
            return text, "open access", url
    return "", "", ""


__all__ = ["folders", "index", "library_text", "local_pdf", "open_access_urls"]
