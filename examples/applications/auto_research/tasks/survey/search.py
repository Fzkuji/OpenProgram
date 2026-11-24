"""Find papers for a field in free sources. Plain code, no model calls.

Every function returns candidates of one shape: {id, title, abstract, date, year,
authors, venue, arxiv, doi, citations, url, found_by}. id is "arXiv:<id>" when the
paper is on arXiv, otherwise "doi:<doi>" or the source's own id, so the same paper
from two sources merges into one candidate.

arXiv answers without a key but wants three seconds between calls. Semantic Scholar
rate-limits hard without S2_API_KEY, and OpenAlex shares a small daily budget per IP
address without OPENALEX_API_KEY, so a source that fails is named in UNAVAILABLE and
the run goes on with the others.
"""

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from auto_research.tasks.citations.sources import UNAVAILABLE, _env, fetch

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_ID = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv\.)(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", re.I)
ARXIV_GAP = 3.1          # seconds between arXiv calls, as arXiv asks
_last_arxiv = [0.0]


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def candidate(title, abstract="", date="", authors=(), venue="", arxiv="", doi="", citations=None,
              url="", found_by="", openalex="", s2=""):
    arxiv = re.sub(r"v\d+$", "", arxiv or "")
    doi = (doi or "").lower().replace("https://doi.org/", "")
    if not arxiv and doi.startswith("10.48550/arxiv."):
        arxiv = doi.split("arxiv.", 1)[1]
    pid = f"arXiv:{arxiv}" if arxiv else f"doi:{doi}" if doi else openalex or s2 or "t:" + norm_title(title)[:60]
    date = (date or "")[:10]
    return {"id": pid, "title": re.sub(r"\s+", " ", title or "").strip().rstrip("."),
            "abstract": re.sub(r"\s+", " ", abstract or "").strip(), "date": date,
            "year": int(date[:4]) if date[:4].isdigit() else None, "authors": [a for a in authors if a][:12],
            "venue": venue or "", "arxiv": arxiv, "doi": doi, "citations": citations,
            "url": url or (f"https://arxiv.org/abs/{arxiv}" if arxiv else f"https://doi.org/{doi}" if doi else ""),
            "found_by": [found_by] if found_by else [], "openalex": openalex, "s2": s2}


def merge(groups) -> list[dict]:
    """One candidate per paper: by id first, then by normalized title. Later sources
    fill fields the earlier ones left empty and add to found_by."""
    out, by_id, by_title = [], {}, {}
    for c in (c for group in groups for c in group):
        if not c["title"]:
            continue
        key = norm_title(c["title"])
        hit = by_id.get(c["id"]) or by_title.get(key)
        if hit is None:
            out.append(c)
            by_id[c["id"]], by_title[key] = c, c
            continue
        for field, value in c.items():
            if field == "found_by":
                hit["found_by"] += [s for s in value if s not in hit["found_by"]]
            elif field == "citations":
                known = [v for v in (hit["citations"], value) if v is not None]
                hit["citations"] = max(known) if known else None
            elif not hit.get(field) and value:
                hit[field] = value
        if hit["arxiv"] and not hit["id"].startswith("arXiv:"):
            by_id.pop(hit["id"], None)
            hit["id"] = f"arXiv:{hit['arxiv']}"
            by_id[hit["id"]] = hit
    return out


# arXiv

def arxiv_search(query: str, limit: int = 50, since: int = 0) -> list[dict]:
    """The arXiv API on titles and abstracts; every query word must appear."""
    words = [w for w in re.findall(r"[A-Za-z0-9\-]{2,}", query) if w.lower() not in {"and", "or", "not"}]
    if not words:
        return []
    expr = " AND ".join(f"all:{w}" for w in words[:8])
    found = _arxiv({"search_query": expr, "max_results": limit, "sortBy": "relevance"}, "arxiv")
    return [c for c in found if not since or (c["year"] or 0) >= since]


def arxiv_ids(ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        out += _arxiv({"id_list": ",".join(ids[i:i + 50]), "max_results": 50}, "arxiv id")
    return out


def _arxiv(params: dict, how: str) -> list[dict]:
    wait = _last_arxiv[0] + ARXIV_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    body = fetch("https://export.arxiv.org/api/query", params, as_json=False, label="arXiv")
    _last_arxiv[0] = time.monotonic()
    try:
        entries = list(ET.fromstring(body).iter(ATOM + "entry")) if body else []
    except ET.ParseError:
        return []
    out = []
    for e in entries:
        title = e.findtext(ATOM + "title") or ""
        link = e.findtext(ATOM + "id") or ""
        m = ARXIV_ID.search(link)
        if title.strip() in ("", "Error") or not m:
            continue
        out.append(candidate(title, e.findtext(ATOM + "summary"), e.findtext(ATOM + "published"),
                             [a.findtext(ATOM + "name") for a in e.iter(ATOM + "author")], "arXiv",
                             arxiv=m.group(1), found_by=how))
    return out


# Semantic Scholar

S2_FIELDS = "title,abstract,year,publicationDate,externalIds,citationCount,venue,authors,url"


def s2_search(query: str, limit: int = 50, since: int = 0) -> list[dict]:
    params = {"query": query, "limit": limit, "fields": S2_FIELDS}
    if since:
        params["year"] = f"{since}-"
    d = fetch("https://api.semanticscholar.org/graph/v1/paper/search", params, label="Semantic Scholar",
              headers=_env("S2_API_KEY", "x-api-key"))
    return [_s2(p, "semantic scholar") for p in (d or {}).get("data") or []]


def _s2(p: dict, how: str) -> dict:
    ext = p.get("externalIds") or {}
    return candidate(p.get("title"), p.get("abstract"), p.get("publicationDate") or str(p.get("year") or ""),
                     [a.get("name") for a in p.get("authors") or []], p.get("venue"), ext.get("ArXiv", ""),
                     ext.get("DOI", ""), p.get("citationCount"), p.get("url"), how, s2=p.get("paperId", ""))


# OpenAlex

OA_FIELDS = "id,doi,title,publication_date,cited_by_count,type,locations,authorships,abstract_inverted_index,primary_location"


def openalex_search(query: str, limit: int = 50, since: int = 0, kind: str = "") -> list[dict]:
    filters = [f"title_and_abstract.search:{query.replace(',', ' ')}"]
    if since:
        filters.append(f"from_publication_date:{since}-01-01")
    if kind:
        filters.append(f"type:{kind}")
    return _openalex({"filter": ",".join(filters), "per-page": limit, "select": OA_FIELDS}, "openalex")


def openalex_citing(work: str, limit: int = 25) -> list[dict]:
    """Works that cite one OpenAlex work, most cited first: the forward snowball."""
    return _openalex({"filter": f"cites:{work.rsplit('/', 1)[-1]}", "sort": "cited_by_count:desc",
                      "per-page": limit, "select": OA_FIELDS}, "cited by seed")


def openalex_works(ids: list[str]) -> list[dict]:
    """Works by OpenAlex id, fifty per call: the backward snowball."""
    out = []
    for i in range(0, len(ids), 50):
        out += _openalex({"filter": "openalex_id:" + "|".join(w.rsplit("/", 1)[-1] for w in ids[i:i + 50]),
                          "per-page": 50, "select": OA_FIELDS}, "cited by seed")
    return out


def openalex_references(work: str) -> list[str]:
    d = fetch(f"https://api.openalex.org/works/{work.rsplit('/', 1)[-1]}",
              {"select": "referenced_works", **_env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return (d or {}).get("referenced_works") or []


def yearly_counts(query: str, since: int) -> dict[int, int]:
    """How many works match the query in each year since `since`, over all of OpenAlex."""
    d = fetch("https://api.openalex.org/works", {"filter": f"title_and_abstract.search:{query.replace(',', ' ')},"
                                                           f"from_publication_date:{since}-01-01",
                                                 "group_by": "publication_year",
                                                 **_env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    return {int(g["key"]): g["count"] for g in (d or {}).get("group_by") or [] if str(g.get("key")).isdigit()}


def _openalex(params: dict, how: str) -> list[dict]:
    d = fetch("https://api.openalex.org/works", {**params, **_env("OPENALEX_API_KEY", "api_key")}, label="OpenAlex")
    out = []
    for w in (d or {}).get("results") or []:
        urls = [l.get("landing_page_url") or "" for l in w.get("locations") or []] + [w.get("doi") or ""]
        arxiv = next((m.group(1) for u in urls if (m := ARXIV_ID.search(u))), "")
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        out.append(candidate(w.get("title"), abstract_text(w.get("abstract_inverted_index")), w.get("publication_date"),
                             [a["author"]["display_name"] for a in w.get("authorships") or []], src, arxiv,
                             w.get("doi"), w.get("cited_by_count"), "", how, openalex=w.get("id", "")))
        out[-1]["type"] = w.get("type") or ""
    return out


def abstract_text(index: dict | None) -> str:
    if not index:
        return ""
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words)


SEARCHES = {"arxiv": arxiv_search, "semantic scholar": s2_search, "openalex": openalex_search}


def search_all(queries: list[str], since: int, per_query: int = 50, check=lambda: None) -> tuple[list[dict], dict]:
    """Every query in every source. Returns the merged candidates and, per query, how
    many records each source gave."""
    groups, receipts = [], {}
    for q in queries:
        receipts[q] = {}
        for name, search in SEARCHES.items():
            check()
            found = search(q, per_query, since)
            groups.append(found)
            receipts[q][name] = len(found)
    return merge(groups), receipts


__all__ = ["SEARCHES", "UNAVAILABLE", "arxiv_ids", "arxiv_search", "candidate", "merge", "norm_title",
           "openalex_citing", "openalex_references", "openalex_search", "openalex_works", "s2_search",
           "search_all", "yearly_counts"]
