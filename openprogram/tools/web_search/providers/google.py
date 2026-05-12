"""Google Programmable Search Engine (PSE) provider.

Real Google index, but you have to create a PSE first (10-minute setup
at https://programmablesearchengine.google.com/). Requires
``GOOGLE_PSE_API_KEY`` + ``GOOGLE_PSE_CX`` (the engine id). Free tier:
100 queries/day.

Docs: https://developers.google.com/custom-search/v1/overview
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://www.googleapis.com/customsearch/v1"
TIMEOUT = 20.0


@dataclass
class GoogleProvider:
    name: str = "google"
    priority: int = 80
    # Both keys are required — the registry uses ``requires_env`` only
    # for "configured?" display, and ``is_available()`` enforces them
    # both.
    requires_env: tuple = ("GOOGLE_PSE_API_KEY", "GOOGLE_PSE_CX")

    def is_available(self) -> bool:
        return bool(os.environ.get("GOOGLE_PSE_API_KEY")
                    and os.environ.get("GOOGLE_PSE_CX"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("GOOGLE_PSE_API_KEY", "")
        cx = os.environ.get("GOOGLE_PSE_CX", "")
        if not key or not cx:
            raise RuntimeError("GOOGLE_PSE_API_KEY + GOOGLE_PSE_CX not set")
        # PSE's `num` is 1-10 per page; for >10 we'd need pagination
        # via `start`. Keep this simple and cap at 10 — agents that
        # need more should run the query twice.
        params = urllib.parse.urlencode({
            "key": key,
            "cx": cx,
            "q": query,
            "num": max(1, min(int(num_results), 10)),
            "safe": "off",
        })
        req = urllib.request.Request(
            f"{API_URL}?{params}",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            raise RuntimeError(f"Google PSE HTTP {e.code}: {body}") from e
        results: list[SearchResult] = []
        for r in data.get("items", []) or []:
            results.append(SearchResult(
                title=str(r.get("title", "")),
                url=str(r.get("link", "")),
                snippet=str(r.get("snippet", "")),
                extras={
                    "displayLink": r.get("displayLink"),
                    "formattedUrl": r.get("formattedUrl"),
                },
            ))
        return results
