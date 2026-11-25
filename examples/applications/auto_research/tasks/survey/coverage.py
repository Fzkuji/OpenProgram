"""How much of a field the search found: a capture-recapture estimate from the overlap
of the keyword sources. Plain code, no model calls.

Two sources that search the same field independently find some papers in common. With
n1 and n2 relevant papers found by each and m found by both, the field holds about
(n1 + 1)(n2 + 1) / (m + 1) - 1 relevant papers (Chapman's estimator). The sources use
the same queries, so they are not independent and the estimate of the field is low;
the coverage it gives is therefore an upper bound, and the largest estimate over all
pairs of sources is used.
"""

from itertools import combinations

from .search import SEARCHES

LOW = 0.6           # below this the report asks for more queries


def chapman(n1: int, n2: int, m: int) -> float:
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def estimate(papers: list[dict], sources=tuple(SEARCHES)) -> dict:
    """papers: the relevant papers found, each with found_by (the sources that returned
    it). Returns found, estimated (None when no two sources overlap), coverage and,
    per source, how many it found."""
    sets = {s: {i for i, p in enumerate(papers) if s in (p.get("found_by") or [])} for s in sources}
    by_source = {s: len(v) for s, v in sets.items()}
    found = len(papers)
    keyword = set().union(*sets.values()) if sets else set()
    guesses = [chapman(len(sets[a]), len(sets[b]), len(sets[a] & sets[b]))
               for a, b in combinations(sources, 2) if sets[a] and sets[b] and sets[a] & sets[b]]
    if not guesses:
        return {"found": found, "estimated": None, "coverage": None, "by_source": by_source,
                "only_one_source": sum(sum(i in v for v in sets.values()) == 1 for i in keyword)}
    estimated = max(max(guesses), found)
    return {"found": found, "estimated": round(estimated), "coverage": round(min(1.0, found / estimated), 2),
            "by_source": by_source, "only_one_source": sum(sum(i in v for v in sets.values()) == 1 for i in keyword)}


def note(est: dict) -> str:
    """A note for the run's status when the search looks thin, else "". Coverage that
    cannot be estimated is shown in the report, not counted against the run."""
    if est["coverage"] is not None and est["coverage"] < LOW:
        return (f"the search found about {round(est['coverage'] * 100)}% of an estimated {est['estimated']} "
                f"relevant papers; add queries or seed papers")
    return ""


__all__ = ["LOW", "chapman", "estimate", "note"]
