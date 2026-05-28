"""Kagi Search provider.

Kagi runs an independent index plus their proprietary "T-Rank" relevance
re-ranking; they're a paid-only product (subscription + per-search) but
the quality is well above the free-tier engines, especially on
technical and academic queries. Worth pinning as the high-priority
default when a Kagi key is set.

Requires ``KAGI_API_KEY``. The Kagi Search API is a paid add-on on top
of the regular subscription — see https://help.kagi.com/kagi/api/search.html
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .._http import get_json
from ..registry import SearchResult


API_URL = "https://kagi.com/api/v0/search"
TIMEOUT = 25.0


@dataclass
class KagiProvider:
    name: str = "kagi"
    # Highest non-AI tier — when a Kagi key is set, the user has paid
    # explicitly for high-quality search and probably wants it picked
    # by default over the free engines. Below Tavily (100) only
    # because Tavily's response shape is more agent-optimised for
    # short-answer Q&A.
    priority: int = 93
    requires_env: tuple = ("KAGI_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("KAGI_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("KAGI_API_KEY", "")
        if not key:
            raise RuntimeError("KAGI_API_KEY not set")
        data = get_json(
            API_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bot {key}",
            },
            params={
                "q": query,
                # ``limit`` is 1-10 per result type; cap at 10.
                "limit": max(1, min(int(num_results), 10)),
            },
            timeout=TIMEOUT,
            provider_label="Kagi",
        )
        results: list[SearchResult] = []
        # ``data`` is a list of entries with ``t`` (type) — 0 = search
        # result, 1 = related, 2 = "fast answer". We only consume
        # type 0; type 2 would map nicely into ``snippet`` for
        # answer-style queries but it's request-dependent and
        # inconsistent so leave it out.
        for r in (data.get("data") or []):
            if r.get("t") != 0:
                continue
            results.append(SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or ""),
                snippet=str(r.get("snippet") or "")[:500],
                extras={
                    "thumbnail": (r.get("thumbnail") or {}).get("url"),
                    "published": r.get("published"),
                },
            ))
            if len(results) >= max(1, int(num_results)):
                break
        return results
