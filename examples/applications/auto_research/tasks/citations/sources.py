"""Free bibliographic sources. Every function returns records of one shape:
{source, title, authors, year, venue, doi, url}. A source that fails returns [].

DBLP and DuckDuckGo are left out: both answer scripted requests with a bot
challenge. No source here costs money. Without keys OpenAlex shares one small
daily budget per IP address and Semantic Scholar rate-limits hard; both give
free keys, read from OPENALEX_API_KEY and S2_API_KEY when set. CROSSREF_MAILTO
puts Crossref requests in its polite pool. A source that still fails is named
in UNAVAILABLE so the report can say which look-ups did not run.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "openprogram-citation-check/0.1 (reference pre-check)"}
TIMEOUT = 20
UNAVAILABLE: set[str] = set()
_lock = threading.Lock()


def fetch(url: str, params: dict | None = None, as_json: bool = True, tries: int = 3,
          label: str = "", headers: dict | None = None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
        except Exception:  # noqa: S110  a timeout is retried below
            pass
        time.sleep(1.5 * (attempt + 1))      # 429 and timeouts: back off and retry
    if label:
        with _lock:
            UNAVAILABLE.add(label)
    return None


def _env(name: str, key: str) -> dict:
    return {key: os.environ[name]} if os.environ.get(name) else {}


def record(source, title, authors, year, venue="", doi="", url=""):
    # some records give authors as {"name": ...} objects or carry non-string venues
    names = [a.get("name") or a.get("display_name") or "" if isinstance(a, dict) else str(a or "")
             for a in authors or []]
    text = lambda v: v if isinstance(v, str) else ""
    return {"source": source, "title": re.sub(r"\s+", " ", text(title)).strip().rstrip("."),
            "authors": [n.strip() for n in names if n.strip()],
            "year": int(year) if str(year or "").isdigit() else None,
            "venue": text(venue), "doi": text(doi).lower(), "url": text(url)}


def _crossref(m: dict) -> dict:
    year = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return record("crossref", (m.get("title") or [""])[0],
                  [f"{a.get('given', '')} {a.get('family', '')}" for a in m.get("author", [])],
                  year, (m.get("container-title") or [""])[0], m.get("DOI"), m.get("URL"))


def crossref_doi(doi: str) -> list[dict]:
    d = fetch("https://api.crossref.org/works/" + urllib.parse.quote(doi), _env("CROSSREF_MAILTO", "mailto"),
              label="Crossref")
    return [_crossref(d["message"])] if d else []


def crossref(query: str) -> list[dict]:
    d = fetch("https://api.crossref.org/works", {"query.bibliographic": query, "rows": 5,
                                                 **_env("CROSSREF_MAILTO", "mailto")}, label="Crossref")
    return [_crossref(m) for m in (d or {}).get("message", {}).get("items", [])]


def openalex(query: str) -> list[dict]:
    d = fetch("https://api.openalex.org/works", {"search": query, "per-page": 5,
                                                **_env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    out = []
    for w in (d or {}).get("results", []):
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        out.append(record("openalex", w.get("title"),
                          [a["author"]["display_name"] for a in w.get("authorships", [])],
                          w.get("publication_year"), src,
                          (w.get("doi") or "").replace("https://doi.org/", ""), w.get("id")))
    return out


def semantic_scholar(query: str) -> list[dict]:
    d = fetch("https://api.semanticscholar.org/graph/v1/paper/search",
              {"query": query, "limit": 5, "fields": "title,authors,year,venue,externalIds,url"},
              label="Semantic Scholar", headers=_env("S2_API_KEY", "x-api-key"))
    return [record("semanticscholar", p.get("title"), [a["name"] for a in p.get("authors", [])],
                   p.get("year"), p.get("venue"), (p.get("externalIds") or {}).get("DOI"), p.get("url"))
            for p in (d or {}).get("data", [])]


ATOM = "{http://www.w3.org/2005/Atom}"


def _arxiv(params: dict) -> list[dict]:
    body = fetch("https://export.arxiv.org/api/query", params, as_json=False, label="arXiv")
    out = []
    try:
        entries = list(ET.fromstring(body).iter(ATOM + "entry")) if body else []
    except ET.ParseError:
        return out
    for e in entries:
        title = e.findtext(ATOM + "title") or ""
        if title.strip() in ("", "Error"):
            continue
        out.append(record("arxiv", title, [a.findtext(ATOM + "name") for a in e.iter(ATOM + "author")],
                          (e.findtext(ATOM + "published") or "")[:4], "arXiv", "", e.findtext(ATOM + "id")))
    return out


def arxiv_id(identifier: str) -> list[dict]:
    return _arxiv({"id_list": identifier})


STOP = {"and", "the", "for", "with", "from", "via", "into", "are", "not", "its", "our", "using", "towards"}


def arxiv(query: str) -> list[dict]:
    # "and"/"or"/"not" would be read as operators; eight content words are enough
    kept = [w for w in re.findall(r"[A-Za-z0-9]{3,}", query) if w.lower() not in STOP][:8]
    # every word must match, so one misspelled word empties the search: retry on each half
    for words in (kept, kept[:len(kept) // 2], kept[len(kept) // 2:]):
        if len(words) >= 2 or words is kept and words:
            found = _arxiv({"search_query": " AND ".join(f"ti:{w}" for w in words), "max_results": 5})
            if found:
                return found
    return []


def openreview(query: str) -> list[dict]:
    """Accepted ICLR / NeurIPS / workshop papers; the venue field says 'Submitted' otherwise."""
    d = fetch("https://api2.openreview.net/notes/search",
              {"term": query, "type": "terms", "content": "title", "source": "forum", "limit": 5},
              label="OpenReview")
    out = []
    for n in (d or {}).get("notes", []):
        c = n.get("content", {})
        v = lambda k: (c.get(k) or {}).get("value")
        year = time.gmtime((n.get("pdate") or n.get("cdate") or 0) / 1000).tm_year if (n.get("pdate") or n.get("cdate")) else None
        out.append(record("openreview", v("title"), v("authors") or [], year, v("venue") or "",
                          "", f"https://openreview.net/forum?id={n.get('forum', '')}"))
    return out


def openlibrary(query: str) -> list[dict]:
    """Books, for references that are not papers."""
    d = fetch("https://openlibrary.org/search.json", {"q": query, "limit": 5,
                                                      "fields": "title,author_name,first_publish_year,publisher,key"},
              label="Open Library")
    return [record("openlibrary", b.get("title"), b.get("author_name") or [], b.get("first_publish_year"),
                   ", ".join((b.get("publisher") or [])[:1]), "", "https://openlibrary.org" + b.get("key", ""))
            for b in (d or {}).get("docs", [])]


# Cheap, precise sources first; the rest only when these do not settle a reference.
FIRST = (openalex, semantic_scholar)
WIDER = (crossref, arxiv, openreview, openlibrary)
SEARCHES = {f.__name__: f for f in FIRST + WIDER}
