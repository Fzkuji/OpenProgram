"""The OpenAlex and ORCID look-ups of peer_feedback. Plain code, no model calls.

Every request goes through citations.sources.fetch, so a source that fails is named in
UNAVAILABLE (the run then reports PASS_WITH_NOTES) and tests replace `fetch` alone.
"""

import re
from datetime import date

from auto_research.tasks.citations import sources
from auto_research.tasks.survey.search import abstract_text

API = "https://api.openalex.org"
WORK_FIELDS = "id,title,publication_year,authorships,abstract_inverted_index"
AUTHOR_FIELDS = ("id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,summary_stats,"
                 "last_known_institutions,counts_by_year")
PER_PAGE = 25
COAUTHOR_PAGES = 5          # 200 works a page: enough for five years of almost anyone


def short(oa_id: str) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def _key() -> dict:
    return sources._env("OPENALEX_API_KEY", "api_key")


def _work(w: dict, how: str) -> dict:
    authors = []
    for a in w.get("authorships") or []:
        person = a.get("author") or {}
        if person.get("id"):
            authors.append({"id": short(person["id"]), "name": person.get("display_name") or "",
                            "institutions": [short(i.get("id")) for i in a.get("institutions") or [] if i.get("id")]})
    return {"id": short(w.get("id")), "title": " ".join(str(w.get("title") or "").split()),
            "year": w.get("publication_year") or 0, "authors": authors, "how": how,
            "abstract": abstract_text(w.get("abstract_inverted_index"))[:900]}


def search_works(query: str, how: str) -> list[dict]:
    """The most relevant works for a free-text query, with their authors."""
    if not query.strip():
        return []
    d = sources.fetch(API + "/works", {"search": query, "per-page": PER_PAGE, "select": WORK_FIELDS, **_key()},
                      label="OpenAlex")
    return [_work(w, how) for w in (d or {}).get("results") or [] if w.get("id")]


def works_by_id(ids: list[str], how: str) -> list[dict]:
    """Works by OpenAlex id, fifty per request."""
    out = []
    ids = [short(i) for i in ids if i]
    for i in range(0, len(ids), 50):
        d = sources.fetch(API + "/works", {"filter": "openalex_id:" + "|".join(ids[i:i + 50]), "per-page": 50,
                                           "select": WORK_FIELDS, **_key()}, label="OpenAlex")
        out += [_work(w, how) for w in (d or {}).get("results") or [] if w.get("id")]
    return out


def _author(a: dict) -> dict:
    stats = a.get("summary_stats") or {}
    return {"id": short(a.get("id")), "name": a.get("display_name") or "",
            "alternatives": [str(n) for n in a.get("display_name_alternatives") or []],
            "orcid": a.get("orcid") or "", "works_count": a.get("works_count") or 0,
            "cited_by_count": a.get("cited_by_count") or 0, "h_index": stats.get("h_index") or 0,
            "institutions": [{"id": short(i.get("id")), "name": i.get("display_name") or "",
                              "type": i.get("type") or "", "lineage": [short(x) for x in i.get("lineage") or []]}
                             for i in a.get("last_known_institutions") or [] if i.get("id")],
            "by_year": {int(c["year"]): c.get("works_count") or 0 for c in a.get("counts_by_year") or []
                        if str(c.get("year", "")).isdigit()}}


def search_authors(name: str) -> list[dict]:
    d = sources.fetch(API + "/authors", {"search": name, "per-page": 10, "select": AUTHOR_FIELDS, **_key()},
                      label="OpenAlex")
    return [_author(a) for a in (d or {}).get("results") or [] if a.get("id")]


def authors_by_id(ids: list[str]) -> dict[str, dict]:
    """Author profiles by OpenAlex id, fifty per request."""
    out = {}
    ids = [short(i) for i in ids if i]
    for i in range(0, len(ids), 50):
        d = sources.fetch(API + "/authors", {"filter": "openalex_id:" + "|".join(ids[i:i + 50]), "per-page": 50,
                                             "select": AUTHOR_FIELDS, **_key()}, label="OpenAlex")
        for a in (d or {}).get("results") or []:
            if a.get("id"):
                out[short(a["id"])] = _author(a)
    return out


def recent_works(author_ids: list[str], years: int, today: date) -> list[dict]:
    """The works of these authors published in the last `years` years, with their authors."""
    if not author_ids:
        return []
    since = today.replace(year=today.year - years) if not (today.month == 2 and today.day == 29) \
        else today.replace(year=today.year - years, day=28)
    out, cursor = [], "*"
    for _ in range(COAUTHOR_PAGES):
        d = sources.fetch(API + "/works", {"filter": f"author.id:{'|'.join(author_ids)},"
                                                     f"from_publication_date:{since.isoformat()}",
                                           "per-page": 200, "cursor": cursor,
                                           "select": "id,title,publication_year,authorships", **_key()},
                          label="OpenAlex")
        if not d:
            break
        out += [_work(w, "coauthor") for w in d.get("results") or [] if w.get("id")]
        cursor = (d.get("meta") or {}).get("next_cursor")
        if not cursor or not d.get("results"):
            break
    return out


ORCID_ID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")


def orcid_links(orcid: str) -> list[dict]:
    """The public researcher URLs (home pages) of an ORCID record. Emails are never read."""
    m = ORCID_ID.search(orcid or "")
    if not m:
        return []
    d = sources.fetch(f"https://pub.orcid.org/v3.0/{m.group(1)}/researcher-urls",
                      headers={"Accept": "application/json"}, label="ORCID")
    out = []
    for u in (d or {}).get("researcher-url") or []:
        url = ((u.get("url") or {}).get("value") or "").strip()
        if url.startswith(("http://", "https://")):
            out.append({"name": (u.get("url-name") or "").strip(), "url": url})
    return out


__all__ = ["authors_by_id", "orcid_links", "recent_works", "search_authors", "search_works", "short",
           "works_by_id"]
