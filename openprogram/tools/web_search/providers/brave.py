"""Brave Search API provider.

Independent index (not bing/google rerank), good privacy story. Requires
``BRAVE_API_KEY``. Free tier (Data for AI plan): 2000 queries/month.

Docs: https://api.search.brave.com/app/documentation/web-search/get-started
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT = 20.0


@dataclass
class BraveProvider:
    name: str = "brave"
    priority: int = 85
    requires_env: tuple = ("BRAVE_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("BRAVE_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("BRAVE_API_KEY", "")
        if not key:
            raise RuntimeError("BRAVE_API_KEY not set")
        params = urllib.parse.urlencode({
            "q": query,
            # Brave's count is 1-20 for the regular endpoint.
            "count": max(1, min(int(num_results), 20)),
            "safesearch": "moderate",
        })
        req = urllib.request.Request(
            f"{API_URL}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
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
            raise RuntimeError(f"Brave HTTP {e.code}: {body}") from e
        web = data.get("web") or {}
        results: list[SearchResult] = []
        for r in web.get("results", []) or []:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("description", "")),
                extras={
                    "age": r.get("age"),
                    "language": r.get("language"),
                },
            ))
        return results
