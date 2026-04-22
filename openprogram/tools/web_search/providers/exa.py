"""Exa web search provider.

Exa is a neural search engine — good at semantic queries where keyword
matching falls short (e.g. "blog posts similar to this one"). Requires
EXA_API_KEY.

Docs: https://docs.exa.ai/reference/search
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://api.exa.ai/search"
TIMEOUT = 20.0


@dataclass
class ExaProvider:
    name: str = "exa"
    priority: int = 90
    requires_env: tuple = ("EXA_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("EXA_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("EXA_API_KEY", "")
        if not key:
            raise RuntimeError("EXA_API_KEY not set")
        payload = json.dumps({
            "query": query,
            "numResults": max(1, min(int(num_results), 25)),
            # "auto" lets Exa decide between keyword and neural per query
            "type": "auto",
            "contents": {
                "text": {"maxCharacters": 500, "includeHtmlTags": False},
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
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
            raise RuntimeError(f"Exa HTTP {e.code}: {body}") from e
        results: list[SearchResult] = []
        for r in data.get("results", []):
            snippet = r.get("text") or r.get("summary") or ""
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(snippet)[:500],
                extras={
                    "score": r.get("score"),
                    "publishedDate": r.get("publishedDate"),
                },
            ))
        return results
