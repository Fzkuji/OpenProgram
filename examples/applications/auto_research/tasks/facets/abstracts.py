"""The abstracts of the works in the project's .bib. Plain code, no model calls.

Semantic Scholar's batch API first (one POST for every entry with an arXiv id or a DOI,
through scouting.common.post), then OpenAlex for the rest (by DOI, else a title search
whose best hit matches the title closely), then the arXiv API for entries with an arXiv
id. Every GET goes through citations.sources.fetch, so a source that fails is named in
UNAVAILABLE and the run goes on.
"""

from auto_research.tasks.citations import sources
from auto_research.tasks.citations.refs import AUTHENTIC_TITLE, title_sim
from auto_research.tasks.scouting.common import post
from auto_research.tasks.survey import search

S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"


def s2_id(entry: dict) -> str:
    if entry.get("arxiv"):
        return "ARXIV:" + entry["arxiv"]
    return "DOI:" + entry["doi"] if entry.get("doi") else ""


def from_s2(entries: list[dict]) -> dict[str, tuple[str, str]]:
    ids = [(e["key"], s2_id(e)) for e in entries if s2_id(e)]
    found = {}
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        d = post(S2_BATCH, {"fields": "title,abstract"}, {"ids": [p for _, p in part]}, label="Semantic Scholar",
                 headers=sources._env("S2_API_KEY", "x-api-key"))
        for (key, _), rec in zip(part, d if isinstance(d, list) else []):
            if isinstance(rec, dict) and rec.get("abstract"):
                found[key] = (" ".join(rec["abstract"].split()), "Semantic Scholar")
    return found


def from_openalex(entry: dict) -> str:
    if entry.get("doi"):
        w = sources.fetch(f"https://api.openalex.org/works/doi:{entry['doi']}",
                          {"select": "title,abstract_inverted_index", **sources._env("OPENALEX_API_KEY", "api_key")},
                          label="OpenAlex") or {}
        text = search.abstract_text(w.get("abstract_inverted_index"))
        if text:
            return text
    if entry.get("title"):
        hits = search.openalex_search(entry["title"], 5)
        best = max(hits, key=lambda c: title_sim(entry["title"], c["title"]), default=None)
        if best and best["abstract"] and title_sim(entry["title"], best["title"]) >= AUTHENTIC_TITLE:
            return best["abstract"]
    return ""


def abstracts(entries: list[dict]) -> dict[str, tuple[str, str]]:
    """key -> (abstract, source) for every entry whose abstract was found."""
    found = from_s2(entries)
    for e in entries:
        if e["key"] in found:
            continue
        text = from_openalex(e)
        if text:
            found[e["key"]] = (" ".join(text.split()), "OpenAlex")
    left = [e for e in entries if e["key"] not in found and e.get("arxiv")]
    if left:
        by_id = {c["arxiv"]: c for c in search.arxiv_ids([e["arxiv"] for e in left])}
        for e in left:
            c = by_id.get(e["arxiv"])
            if c and c["abstract"]:
                found[e["key"]] = (c["abstract"], "arXiv")
    return found


__all__ = ["abstracts"]
