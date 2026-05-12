"""Firecrawl search provider.

Firecrawl returns SERP-style results AND full page content (no need to
follow up with a fetch). Requires ``FIRECRAWL_API_KEY``. Free tier:
500 credits/month.

Docs: https://docs.firecrawl.dev/features/search
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://api.firecrawl.dev/v1/search"
TIMEOUT = 30.0


@dataclass
class FirecrawlProvider:
    name: str = "firecrawl"
    priority: int = 75
    requires_env: tuple = ("FIRECRAWL_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("FIRECRAWL_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not key:
            raise RuntimeError("FIRECRAWL_API_KEY not set")
        payload = json.dumps({
            "query": query,
            "limit": max(1, min(int(num_results), 20)),
            # Don't ask for full markdown by default — that's a fetch
            # tool's job and bloats the response 10x. Callers who want
            # body content should call ``firecrawl/scrape`` separately.
            "scrapeOptions": {},
        }).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
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
            raise RuntimeError(f"Firecrawl HTTP {e.code}: {body}") from e
        rows = data.get("data") or data.get("web") or []
        results: list[SearchResult] = []
        for r in rows:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("description", "") or r.get("snippet", "")),
                extras={"position": r.get("position")},
            ))
        return results
