"""web_search tool — keyword → list of relevant URLs.

Pairs with ``web_fetch``: web_search gets the agent the URLs, web_fetch
reads them. The tool itself is small — it just:

  1. Picks a backend via ``registry.select(prefer=provider)``. Default
     priority order: Tavily → Exa → DuckDuckGo.
  2. Delegates the actual search to that backend.
  3. Formats results into a stable numbered-list string the agent can
     scan (title, URL, 1-2 sentence snippet).

Provider backends live in ``web_search/providers/`` — each one is a
small dataclass with ``name / priority / requires_env / is_available /
search`` methods. Adding a new provider (Brave, Serper, Perplexity…)
is a single file and one registry.register() call.
"""

from __future__ import annotations

from typing import Any

from .._helpers import is_available as _tool_is_available
from .._helpers import read_int_param, read_string_param
from . import providers as _  # registers builtins on import  # noqa: F401
from .registry import SearchResult, registry


NAME = "web_search"

DESCRIPTION = (
    "Search the web and return a ranked list of results (title, URL, "
    "snippet). Use when you have a question and need to discover URLs — "
    "pair with `web_fetch` to read the full page. Pass `provider=tavily|"
    "exa|duckduckgo` to force a specific backend, otherwise the "
    "highest-priority available backend is used (Tavily > Exa > DDG)."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Natural-language is fine for Tavily/Exa; keyword-style works best for DDG.",
            },
            "num_results": {
                "type": "integer",
                "description": "Maximum number of results (default 8, typical cap 20).",
            },
            "provider": {
                "type": "string",
                "description": "Force a specific backend: tavily | exa | duckduckgo. Omit for auto-select by priority + availability.",
            },
        },
        "required": ["query"],
    },
}


def _format(query: str, provider_name: str, results: list[SearchResult]) -> str:
    if not results:
        return f"No results for {query!r} (via {provider_name})."
    lines = [f"# Web search: {query!r}  (via {provider_name}, {len(results)} results)\n"]
    for i, r in enumerate(results, 1):
        snippet = r.snippet.strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:297] + "…"
        lines.append(f"{i}. **{r.title or '(no title)'}** — {r.url}\n   {snippet}")
    return "\n".join(lines)


def _tool_check_fn() -> bool:
    """Hide the tool entirely when no backend is configured."""
    return bool(registry.available())


def execute(
    query: str | None = None,
    num_results: int = 8,
    provider: str | None = None,
    **kw: Any,
) -> str:
    if query is None:
        query = read_string_param(kw, "query", "q")
    if not query:
        return "Error: `query` is required."
    num_results = read_int_param(kw, "num_results", "numResults", default=num_results) or num_results
    num_results = max(1, min(int(num_results), 25))
    provider = read_string_param(kw, "provider", "backend", default=provider)

    try:
        backend = registry.select(prefer=provider)
    except LookupError as e:
        return f"Error: {e}"

    try:
        results = backend.search(query, num_results=num_results)
    except Exception as e:
        return f"Error: {backend.name} search failed: {type(e).__name__}: {e}"

    return _format(query, backend.name, results)


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
