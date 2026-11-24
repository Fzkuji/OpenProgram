"""Look a reference up in the free sources and give the deterministic verdict.

Plain code: network calls, no model calls.
"""

from concurrent.futures import ThreadPoolExecutor

from openprogram.runtime.agent.call_state import check_cancelled

from . import sources
from .refs import AUTHENTIC_TITLE, assess, score, title_sim

WORKERS = 4


def lookup(ref: dict) -> dict:
    """Records for one reference: DOI and arXiv id first, then title searches."""
    records, doi_wrong = [], False
    if ref["doi"]:
        found = sources.crossref_doi(ref["doi"])
        if found and title_sim(ref["title"], found[0]["title"]) >= 0.85:
            records += found
        else:
            doi_wrong = True
    if ref["arxiv"]:
        records += sources.arxiv_id(ref["arxiv"])
    searched = set()
    query = ref["title"]
    for search in sources.FIRST:
        records += search(query)
        searched.add(search.__name__)
        if settled(ref, records):
            break
    else:
        for search in sources.WIDER:
            records += search(query)
            searched.add(search.__name__)
    result = assess(ref, records)
    if "venue_unconfirmed" in result["notices"]:
        # the published version may sit in a source not searched yet; the matched
        # record's title is spelled right even when the cited one is not
        for name in ("openreview", "crossref"):
            if name not in searched or result["match"]["title"].lower() != query.lower():
                records += sources.SEARCHES[name](result["match"]["title"])
        result = assess(ref, records)
    if doi_wrong:
        result["notices"].insert(0, "doi_incorrect")
    result["records"] = records
    return result


def settled(ref: dict, records: list[dict]) -> bool:
    return any(s["title"] >= 0.95 and (s["authors"] is None or s["authors"] >= 0.5)
               for s in (score(ref, r) for r in records))


def lookup_all(refs: list[dict]) -> list[dict]:
    check_cancelled()
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(lookup, refs))
    check_cancelled()
    return results


def search_queries(queries: list[str]) -> list[dict]:
    """Run model-written queries on every source and keep unique records."""
    jobs = [(search, q) for q in queries for search in sources.FIRST + sources.WIDER]
    with ThreadPoolExecutor(WORKERS) as pool:
        batches = list(pool.map(lambda job: job[0](job[1]), jobs))
    seen, out = set(), []
    for rec in (r for batch in batches for r in batch):
        key = (rec["title"].lower(), rec["year"])
        if rec["title"] and key not in seen:
            seen.add(key)
            out.append(rec)
    return out


def closest(ref: dict, records: list[dict], n: int) -> list[dict]:
    """The n records closest to the reference, one per title."""
    seen, out = set(), []
    for rec in sorted(records, key=lambda r: score(ref, r)["total"], reverse=True):
        if rec["title"].lower() not in seen:
            seen.add(rec["title"].lower())
            out.append(rec)
        if len(out) == n:
            break
    return out


__all__ = ["AUTHENTIC_TITLE", "closest", "lookup", "lookup_all", "search_queries"]
