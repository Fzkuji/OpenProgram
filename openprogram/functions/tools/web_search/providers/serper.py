"""Serper.dev web search provider.

Cheapest practical way to hit real Google results from an agent — Serper
proxies Google SERP and returns clean JSON. Requires ``SERPER_API_KEY``.
Free tier: 2,500 queries (one-time, not per month); paid is $50 per 50,000.

Docs: https://serper.dev/api-key
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

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
        payload = json.dumps({
            "q": query,
            # ``num`` is 10/20/30/100. We round up to the nearest valid
            # bucket above ``num_results`` so the agent gets at least
            # what it asked for; we'll truncate the response below.
            "num": _bucket(num_results),
        }).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"Serper HTTP {e.code}: {body}") from e
        results: list[SearchResult] = []
        # Serper returns several sections (organic / answerBox / knowledgeGraph
        # / topStories). We only consume `organic` for the SearchResult shape;
        # the rest is rendering-style metadata that agents either don't need
        # or can pull from a follow-up web_fetch.
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
