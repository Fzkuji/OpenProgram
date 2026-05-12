"""SearXNG provider — self-hosted privacy-respecting meta search.

Aggregates results from Google / Bing / DDG / etc. without sending the
query to any of them yourself. Set ``SEARXNG_URL`` to your instance
(``http://localhost:8888`` is the default for a fresh ``docker run
searxng/searxng``). No API key needed.

Docs: https://docs.searxng.org/dev/search_api.html
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


TIMEOUT = 20.0
DEFAULT_URL = "http://localhost:8888"


@dataclass
class SearxngProvider:
    name: str = "searxng"
    priority: int = 70
    requires_env: tuple = ("SEARXNG_URL",)

    def is_available(self) -> bool:
        # Available as soon as the env var is set. We don't ping the
        # instance here — that would slow every registry scan.
        return bool(os.environ.get("SEARXNG_URL"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        base = (os.environ.get("SEARXNG_URL") or DEFAULT_URL).rstrip("/")
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            # SearXNG returns 10 per page; for >10 hit a second page.
            # Keep it simple and cap at the first page.
            "pageno": 1,
        })
        url = f"{base}/search?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                # Many SearXNG instances rate-limit anonymous UA.
                "User-Agent": "OpenProgram-WebSearch/1.0",
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
            raise RuntimeError(f"SearXNG HTTP {e.code}: {body}") from e
        results: list[SearchResult] = []
        for r in (data.get("results") or [])[: max(1, min(int(num_results), 20))]:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content", "")),
                extras={
                    "engine": r.get("engine"),
                    "score": r.get("score"),
                },
            ))
        return results
