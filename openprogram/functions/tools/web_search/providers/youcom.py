"""You.com web search provider.

You.com's Search API ships clean JSON snippets sized for agents and
includes a separate ``hits.url``/``hits.snippet`` shape per result.
Requires ``YDC_API_KEY`` (the You.com Developer Console env name) or
``YOU_API_KEY`` as a friendlier alias.

Docs: https://documentation.you.com/api-reference/search
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .._http import get_json
from ..registry import SearchResult


API_URL = "https://chat-api.you.com/search"
TIMEOUT = 20.0


@dataclass
class YouComProvider:
    name: str = "youcom"
    # Above Brave-via-Serper (82) — You.com's per-result snippets are
    # designed for agent use and the free tier covers casual usage.
    priority: int = 83
    # Either var works. ``requires_env`` only drives the "configured?"
    # display, so list both so the catalog UI shows both as valid
    # source spellings.
    requires_env: tuple = ("YDC_API_KEY", "YOU_API_KEY")

    def is_available(self) -> bool:
        return bool(_resolve_key())

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = _resolve_key()
        if not key:
            raise RuntimeError("YDC_API_KEY / YOU_API_KEY not set")
        data = get_json(
            API_URL,
            headers={
                "Accept": "application/json",
                "X-API-Key": key,
            },
            params={
                "query": query,
                # ``num_web_results`` cap is 20 per the public docs.
                "num_web_results": max(1, min(int(num_results), 20)),
            },
            timeout=TIMEOUT,
            provider_label="You.com",
        )
        results: list[SearchResult] = []
        # Newer Search-API responses use ``hits`` directly; older /smart
        # endpoint nests under ``search.results``. Cover both.
        rows = data.get("hits") or (data.get("search") or {}).get("results") or []
        for r in rows:
            # ``snippets`` (plural) is preferred — they're individually
            # ranked sentence-level extracts. Fall back to ``description``
            # / ``snippet`` for legacy shape.
            snippet_parts = r.get("snippets") or []
            snippet = (
                " ".join(s for s in snippet_parts if isinstance(s, str))
                if snippet_parts
                else (r.get("description") or r.get("snippet") or "")
            )
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(snippet),
                extras={
                    "favicon_url": r.get("favicon_url"),
                    "thumbnail_url": r.get("thumbnail_url"),
                    "age": r.get("age"),
                },
            ))
        return results


def _resolve_key() -> str | None:
    return os.environ.get("YDC_API_KEY") or os.environ.get("YOU_API_KEY")
