"""Multi-provider aggregation strategies.

Borrowed concept from xynehq/websearch and pi-search-hub: instead of
picking ONE provider via priority + availability, run the query against
several backends concurrently and merge their ranked hit lists into one.

Implements two strategies:

  * **Reciprocal Rank Fusion (RRF)** — the canonical multi-source
    ranking blend. Each URL's final score is the sum of
    ``1 / (k + rank)`` across every provider that surfaced it. Common
    URLs ranked highly by multiple providers float to the top; one-off
    hits drop down naturally. ``k = 60`` is the value the original RRF
    paper recommends and what most search-aggregation projects use.

  * **Race** — return whichever provider's results come back first.
    Useful when latency matters more than coverage (one slow provider
    won't drag down a fast one).

The web_search tool exposes these via ``combine="rrf"`` / ``"race"``
and a ``providers=tavily,brave,exa`` arg.
"""

from __future__ import annotations

import concurrent.futures
from typing import Iterable

from .registry import SearchResult, registry


# Rank-fusion smoothing constant. Per the RRF paper (Cormack et al,
# 2009) and every search-aggregation lib that's adopted RRF since.
# Higher k → ranks matter less / equality between hits matters more.
_RRF_K = 60


def combine_rrf(
    query: str,
    *,
    num_results: int = 8,
    providers: Iterable[str] | None = None,
    timeout: float = 25.0,
) -> tuple[list[SearchResult], list[str]]:
    """Run ``query`` against several providers in parallel; merge via RRF.

    Args:
      query: the search query string.
      num_results: cap on the merged result list.
      providers: names of providers to query. If None, uses every
        ``available()`` provider — a sensible "use everything I can"
        default for high-stakes queries.
      timeout: per-provider deadline. Slow providers get dropped from
        the merge rather than blocking the whole call.

    Returns:
      ``(merged_results, contributing_provider_names)``. The provider
      list is useful for the formatter so the agent can see "via
      tavily + brave + exa" instead of a single backend name.
    """
    backend_names = _resolve_provider_names(providers)
    if not backend_names:
        raise LookupError("No providers available for combined search")

    # Map each provider name → its ranked result list. We keep the rank
    # implicit in the list index (rank-1 = index 0, rank-2 = index 1…).
    raw: dict[str, list[SearchResult]] = {}

    def _query_one(name: str) -> tuple[str, list[SearchResult]]:
        backend = registry.get(name)
        return name, backend.search(query, num_results=num_results)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(backend_names)) as pool:
        futures = {pool.submit(_query_one, n): n for n in backend_names}
        for fut in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                name, hits = fut.result()
            except Exception:
                # Per-provider failure is OK in combine mode — we just
                # drop it from the merge. The other backends still
                # contribute.
                continue
            raw[name] = hits

    if not raw:
        return [], []

    # RRF: for each (url, rank, provider), add 1 / (k + rank) to a
    # running per-url score. Keep the highest-quality canonical
    # title / snippet we see (longer snippet wins as a heuristic).
    score: dict[str, float] = {}
    canonical: dict[str, SearchResult] = {}
    seen_by: dict[str, set[str]] = {}
    for provider_name, hits in raw.items():
        for rank, hit in enumerate(hits, start=1):
            url = _normalise_url(hit.url)
            if not url:
                continue
            score[url] = score.get(url, 0.0) + 1.0 / (_RRF_K + rank)
            seen_by.setdefault(url, set()).add(provider_name)
            prev = canonical.get(url)
            if prev is None or len(hit.snippet) > len(prev.snippet):
                canonical[url] = hit

    # Sort by score desc and cap at num_results.
    ordered_urls = sorted(score, key=score.get, reverse=True)[: max(1, int(num_results))]
    merged: list[SearchResult] = []
    for url in ordered_urls:
        hit = canonical[url]
        # Stamp RRF metadata in extras so callers can introspect why a
        # given hit ranked where it did.
        merged.append(SearchResult(
            title=hit.title,
            url=hit.url,
            snippet=hit.snippet,
            extras={
                **(hit.extras or {}),
                "_rrf_score": round(score[url], 6),
                "_seen_by": sorted(seen_by[url]),
            },
        ))
    return merged, sorted(raw.keys())


def combine_race(
    query: str,
    *,
    num_results: int = 8,
    providers: Iterable[str] | None = None,
    timeout: float = 25.0,
) -> tuple[list[SearchResult], list[str]]:
    """Race strategy — return the first provider's results that arrive.

    Trades coverage for latency: when one of three providers responds in
    300ms and another takes 4s, the agent gets the fast one and moves on.
    The slower ones get cancelled. Useful for time-sensitive interactive
    chat; bad for high-stakes research (use RRF for that).
    """
    backend_names = _resolve_provider_names(providers)
    if not backend_names:
        raise LookupError("No providers available for combined search")

    def _query_one(name: str) -> tuple[str, list[SearchResult]]:
        backend = registry.get(name)
        return name, backend.search(query, num_results=num_results)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(backend_names)) as pool:
        futures = {pool.submit(_query_one, n): n for n in backend_names}
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=timeout):
                try:
                    name, hits = fut.result()
                except Exception:
                    continue
                if hits:
                    # Cancel the others — they may still be running in
                    # the threadpool but their results get discarded.
                    for other in futures:
                        if other is not fut:
                            other.cancel()
                    return hits, [name]
        except concurrent.futures.TimeoutError:
            pass
    return [], []


def _resolve_provider_names(providers: Iterable[str] | None) -> list[str]:
    """Pick which provider names to query in combine mode.

    Explicit list takes precedence; otherwise we use every available
    provider, sorted by registry priority so the "most preferred" ones
    contribute their results first when reporting.
    """
    if providers:
        wanted = [p.strip() for p in providers if p and p.strip()]
        # Drop unknown names rather than raising; the user might pass a
        # combo list and we want partial-success.
        return [p for p in wanted if registry.has(p) and registry.get(p).is_available()]
    return [p.name for p in registry.available()]


def _normalise_url(url: str) -> str:
    """Light URL normalisation so the same page from two providers
    de-dupes during RRF scoring. Strips fragment + trailing slash on
    the path; everything else preserved so query strings (which can
    change content) remain distinct.
    """
    if not url:
        return ""
    url = url.strip()
    # Drop fragment.
    if "#" in url:
        url = url.split("#", 1)[0]
    # Drop one trailing slash from the path, but only if there's no
    # query string (then the slash is part of the path).
    if "?" not in url and url.endswith("/") and url.count("/") > 3:
        url = url[:-1]
    return url
