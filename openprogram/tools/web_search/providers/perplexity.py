"""Perplexity Sonar provider.

Perplexity's API answers a query *with* an LLM-written summary plus
citations — different shape than Brave/Tavily which return raw hits.
We map citations to ``SearchResult`` rows and put the summary in the
first row's snippet (and in ``extras['answer']``) so callers that ignore
extras still see something useful.

Requires ``PERPLEXITY_API_KEY``. Pricing: $0.20 / 1M sonar tokens.

Docs: https://docs.perplexity.ai/api-reference/chat-completions
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..registry import SearchResult


API_URL = "https://api.perplexity.ai/chat/completions"
TIMEOUT = 30.0
MODEL = "sonar"  # fast, cheap; "sonar-pro" is the deeper one


@dataclass
class PerplexityProvider:
    name: str = "perplexity"
    priority: int = 90
    requires_env: tuple = ("PERPLEXITY_API_KEY",)

    def is_available(self) -> bool:
        return bool(os.environ.get("PERPLEXITY_API_KEY"))

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not key:
            raise RuntimeError("PERPLEXITY_API_KEY not set")
        payload = json.dumps({
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return a brief, factual answer in 2-3 sentences. "
                        "Cite sources with [1], [2], etc."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "return_citations": True,
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
            raise RuntimeError(f"Perplexity HTTP {e.code}: {body}") from e

        choices = data.get("choices") or []
        answer = ""
        if choices:
            msg = choices[0].get("message") or {}
            answer = str(msg.get("content") or "")

        # Citations come back either inline in "search_results" or via
        # the older "citations" list (just URLs).
        citations = data.get("search_results") or []
        if not citations:
            urls = data.get("citations") or []
            citations = [{"url": u} for u in urls]

        results: list[SearchResult] = []
        for i, c in enumerate(citations[: max(1, min(int(num_results), 20))]):
            url = str(c.get("url", ""))
            results.append(SearchResult(
                title=str(c.get("title", "") or url),
                url=url,
                # First row carries the LLM answer so a caller looking
                # only at snippets still gets the summary.
                snippet=answer if i == 0 else str(c.get("snippet", "") or ""),
                extras={"answer": answer, "rank": i + 1},
            ))
        # If there were zero citations but the model still answered,
        # surface that as a single row so callers see something.
        if not results and answer:
            results.append(SearchResult(
                title=query,
                url="",
                snippet=answer,
                extras={"answer": answer},
            ))
        return results
