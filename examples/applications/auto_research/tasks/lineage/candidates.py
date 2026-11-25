"""Uncited works that may report a stronger number on a benchmark. Plain code, no
model calls.

Papers with Code's leaderboards are gone, so candidates come from citation data, as
LINEAGE retrieves a baseline lineage: works since `since` that cite both the
benchmark's dataset paper and a cited baseline (OpenAlex `cites:` filters), works that
cite a baseline and name the dataset in their title or abstract, and Semantic
Scholar's search for "<dataset> <metric>". Works already in the .bib are dropped
(DOI, arXiv id, or a title that matches closely); the rest are ranked by citations per
year since publication, then by how many searches found them.
"""

import math
import re
from datetime import date

from ..citations.refs import AUTHENTIC_TITLE, title_sim
from ..library import resolve
from ..library.importer import identities
from ..reading.related import s2_paced
from ..survey import search

PER_QUERY = 25
MAX_SEEDS = 5            # cited baselines used as citation seeds per benchmark
NOT_RESULTS = re.compile(r"\b(survey|review|overview|tutorial|perspectives?|primer|position paper)\b", re.I)
DATASET_KIND = re.compile(r"dataset|database|benchmark|challenge|corpus|suite|evaluation", re.I)


def has_word(title: str, word: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", title or "", re.I))


def dataset_work(dataset: str, refs: list[dict], exclude: set[str]) -> tuple[str, str, str]:
    """(OpenAlex id, title, how) of the paper that introduced the dataset: a .bib entry
    whose title names it, else the most cited OpenAlex work whose title names it and
    calls itself a dataset, benchmark or challenge (else whose title is "<name>: ...")."""
    for ref in refs:
        if ref["key"] not in exclude and has_word(ref.get("title") or "", dataset):
            work, _ = resolve.resolve(ref)
            if work:
                return work["id"], work.get("title") or ref["title"], f"bib {ref['key']}"
    found = search._openalex({"filter": f"title.search:{dataset.replace(',', ' ')}", "sort": "cited_by_count:desc",
                              "per-page": 10, "select": search.OA_FIELDS}, "dataset paper")
    named = [w for w in found if has_word(w["title"], dataset)]
    pick = next((w for w in named if DATASET_KIND.search(w["title"])), None) or \
        next((w for w in named if re.match(rf"{re.escape(dataset)}\s*:", w["title"], re.I)), None)
    return (pick["openalex"], pick["title"], "OpenAlex title search") if pick else ("", "", "")


def baseline_works(keys: list[str], refs: list[dict]) -> dict[str, str]:
    """Cited key -> OpenAlex id, for the keys the library resolves."""
    by_key = {r["key"]: r for r in refs}
    out = {}
    for key in keys[:MAX_SEEDS]:
        if key in by_key:
            work, _ = resolve.resolve(by_key[key])
            if work:
                out[key] = work["id"]
    return out


def citing(filters: list[str], how: str) -> list[dict]:
    return search._openalex({"filter": ",".join(filters), "sort": "cited_by_count:desc", "per-page": PER_QUERY,
                             "select": search.OA_FIELDS}, how)


def gather(bench: dict, refs: list[dict], since: date, dataset_id: str, seeds: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """The merged candidate records of one benchmark and the queries run (with how many
    records each gave)."""
    since_f = f"from_publication_date:{since.isoformat()}"
    short = resolve.short
    queries, groups = [], []

    def run(label, records):
        queries.append({"query": label, "records": len(records)})
        groups.append(records)

    for key, work in seeds.items():
        if dataset_id:
            run(f"cites {short(dataset_id)} and {key}",
                citing([f"cites:{short(dataset_id)}", f"cites:{short(work)}", since_f], f"cites dataset and {key}"))
        run(f"cites {key}, names {bench['dataset']}",
            citing([f"cites:{short(work)}", since_f,
                    f"title_and_abstract.search:{bench['dataset'].replace(',', ' ')}"], f"cites {key}"))
    if dataset_id and not seeds:
        run(f"cites {short(dataset_id)}", citing([f"cites:{short(dataset_id)}", since_f], "cites dataset"))
    q = f"{bench['dataset']} {bench['metric']}"
    run(f"Semantic Scholar: {q}", [r for r in s2_paced(q, 50) if (r["date"] or "") >= since.isoformat()
                                   or (r["year"] or 0) > since.year])
    return search.merge(groups), queries


def known_identities(refs: list[dict]) -> set[str]:
    return {i for r in refs for i in identities(r)}


def in_bib(rec: dict, known: set[str], titles: list[str]) -> bool:
    ids = identities({"doi": rec.get("doi") or "", "arxiv": rec.get("arxiv") or "", "title": rec.get("title") or ""})
    return any(i in known for i in ids) or any(title_sim(rec["title"], t) >= AUTHENTIC_TITLE for t in titles)


def score(rec: dict, today: date) -> float:
    """log10 of citations per year since publication, plus 0.3 per extra search that
    found it: established and recent works first."""
    try:
        published = date.fromisoformat(rec["date"]) if len(rec.get("date") or "") == 10 else date(rec["year"], 7, 1)
    except (TypeError, ValueError):
        published = today
    years = max(0.5, (today - published).days / 365.25)
    return math.log10(1 + (rec.get("citations") or 0) / years) + 0.3 * (len(rec.get("found_by") or []) - 1)


def results_paper(rec: dict) -> bool:
    """Surveys, reviews and tutorials report no results of their own."""
    return rec.get("type") != "review" and not NOT_RESULTS.search(rec.get("title") or "")


def rank(records: list[dict], refs: list[dict], keep: int, today: date) -> tuple[list[dict], int]:
    """The best `keep` records not in the .bib and not surveys, and how many were
    dropped as cited."""
    known, titles = known_identities(refs), [r["title"] for r in refs if r.get("title")]
    fresh = [r for r in records if r.get("title") and not in_bib(r, known, titles)]
    cited = len(records) - len(fresh)
    fresh = [r for r in fresh if results_paper(r)]
    fresh.sort(key=lambda r: (-score(r, today), -(r["year"] or 0)))
    return fresh[:keep], cited


__all__ = ["baseline_works", "dataset_work", "gather", "rank", "score"]
