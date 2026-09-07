"""Jina AI web search provider.

Jina hosts two endpoints aimed at agents:

  * ``s.jina.ai`` — search; returns markdown-formatted result blocks
    with title / url / description per hit. We use this.
  * ``r.jina.ai`` — reader; converts a URL to clean markdown. Used by
    ``web_fetch`` rather than here.

Requires ``JINA_API_KEY`` for the search endpoint.

Docs: https://jina.ai/reader/#apiform — search docs are on the same page.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

from .._http import get_json
from ..registry import SearchResult


API_URL = "https://s.jina.ai/"
TIMEOUT = 25.0


@dataclass
class JinaProvider:
    name: str = "jina"
    priority: int = 35
    requires_env: tuple = ("JINA_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("JINA_API_KEY", "").strip())

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        # ``s.jina.ai`` takes the query as the URL path. JSON response
        # selected via Accept header; without it the endpoint returns
        # markdown (designed for direct LLM consumption).
        url = API_URL + urllib.parse.quote(query, safe="")
        headers = {
            "Accept": "application/json",
            # Skip page-content extraction — we only need the link
            # list (web_fetch handles content if the agent wants it).
            "X-Respond-With": "no-content",
        }
        key = os.environ.get("JINA_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("JINA_API_KEY not set")
        headers["Authorization"] = f"Bearer {key}"
        data = get_json(url, headers=headers, timeout=TIMEOUT, provider_label="Jina")

        # ``data.data`` is the result array. Each entry has title /
        # url / description / content.
        rows: list = []
        if isinstance(data, dict):
            rows = data.get("data") or []
        elif isinstance(data, list):
            rows = data
        results: list[SearchResult] = []
        for r in rows[: max(1, int(num_results))]:
            if not isinstance(r, dict):
                continue
            results.append(SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or r.get("link") or ""),
                snippet=str(r.get("description") or r.get("snippet") or "")[:500],
                extras={
                    "publishedTime": r.get("publishedTime"),
                    "favicon": r.get("favicon"),
                },
            ))
        return results
