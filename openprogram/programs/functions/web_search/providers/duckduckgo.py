"""DuckDuckGo web search provider — zero-key fallback.

DDG has no official search API. We wrap the ``ddgs`` pip package (the
maintained successor to ``duckduckgo_search``) which scrapes their HTML
endpoint. The scrape is fragile — DDG has tweaked their HTML several
times over the years — so treat this as best-effort. Use it for a
demo or when you don't want to hand out API keys, but for serious agent
work set TAVILY_API_KEY or EXA_API_KEY.

The package is an *optional* dep: we do an import check inside
``is_available`` so a machine without ``ddgs`` installed doesn't crash
the registry, it just hides the DDG backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..registry import SearchResult


@dataclass
class DuckDuckGoProvider:
    name: str = "duckduckgo"
    priority: int = 10  # zero-key fallback, lowest quality of the three
    requires_env: tuple = ()

    def is_available(self) -> bool:
        # Don't error if the pip extra isn't installed — just report
        # unavailable so select() skips us.
        try:
            import ddgs  # noqa: F401
            return True
        except Exception:
            return False

    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]:
        try:
            from ddgs import DDGS  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "DuckDuckGo provider requires the `ddgs` pip package. "
                "Install with: pip install ddgs"
            ) from e
        results: list[SearchResult] = []
        # Context-manage the DDGS session so sockets close promptly —
        # important inside long-running agent loops.
        with DDGS() as d:
            hits = d.text(query, max_results=max(1, min(int(num_results), 25)))
            for h in hits or []:
                results.append(SearchResult(
                    title=str(h.get("title", "")),
                    url=str(h.get("href", "") or h.get("url", "")),
                    snippet=str(h.get("body", "") or h.get("snippet", "")),
                ))
        return results
