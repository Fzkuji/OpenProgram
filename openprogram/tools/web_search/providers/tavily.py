"""Tavily web search provider.

Tavily is an LLM-tuned search API — the snippets returned are longer
and pre-summarised, which tends to be what agents actually want.
Requires TAVILY_API_KEY. Free tier: 1000 queries/month.

Docs: https://docs.tavily.com/docs/rest-api/api-reference
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://api.tavily.com/search"
TIMEOUT = 20.0


@dataclass
class TavilyProvider:
    name: str = "tavily"
    priority: int = 100  # highest quality for agent use
    requires_env: tuple = ("TAVILY_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("TAVILY_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("TAVILY_API_KEY", "")
        if not key:
            raise RuntimeError("TAVILY_API_KEY not set")
        payload = json.dumps({
            "api_key": key,
            "query": query,
            "max_results": max(1, min(int(num_results), 20)),
            # `basic` returns snippets; `advanced` is slower but pulls
            # fuller extracts. Default to basic — agents can always
            # web_fetch a URL if they need more body.
            "search_depth": "basic",
            "include_answer": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"Tavily HTTP {e.code}: {body}") from e
        results: list[SearchResult] = []
        for r in data.get("results", []):
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("content", "")),
                extras={"score": r.get("score")},
            ))
        return results
