"""Serper.dev web search provider.

Cheapest practical way to hit real Google results from an agent — Serper
proxies Google SERP and returns clean JSON. Requires ``SERPER_API_KEY``.
Free tier: 2,500 queries (one-time, not per month); paid is $50 per 50,000.

Docs: https://serper.dev/api-key
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .._http import post_json
from ..registry import SearchResult


API_URL = "https://google.serper.dev/search"
TIMEOUT = 20.0


@dataclass
class SerperProvider:
    # Slightly below Brave's 85 because Brave's free tier renews monthly
    # and Serper's is one-shot — bias toward sustainable defaults when
    # both keys are present. Easy to override per call.
    name: str = "serper"
    priority: int = 82
    requires_env: tuple = ("SERPER_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("SERPER_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("SERPER_API_KEY", "")
        if not key:
            raise RuntimeError("SERPER_API_KEY not set")
        data = post_json(
            API_URL,
            headers={"X-API-KEY": key},
            body={
                "q": query,
                # ``num`` is 10/20/30/100. Round up to the nearest valid
                # bucket above ``num_results`` so the agent gets at
                # least what it asked for; we truncate below.
                "num": _bucket(num_results),
            },
            timeout=TIMEOUT,
            provider_label="Serper",
        )
        results: list[SearchResult] = []
        # Serper returns several sections (organic / answerBox /
        # knowledgeGraph / topStories). We only consume ``organic`` for
        # the SearchResult shape; the rest is rendering-style metadata
        # agents either don't need or can pull from a follow-up
        # web_fetch.
        for r in (data.get("organic") or [])[: max(1, int(num_results))]:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("link", "")),
                snippet=str(r.get("snippet", "")),
                extras={
                    "position": r.get("position"),
                    "date": r.get("date"),
                    "sitelinks": r.get("sitelinks"),
                },
            ))
        return results


def _bucket(n: int) -> int:
    """Round ``n`` up to the next Serper-supported result count."""
    for bucket in (10, 20, 30, 100):
        if n <= bucket:
            return bucket
    return 100
