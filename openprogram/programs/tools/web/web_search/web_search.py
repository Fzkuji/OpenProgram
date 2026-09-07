"""web_search tool — keyword → list of relevant URLs.

Pairs with ``web_fetch``: web_search gets the agent the URLs, web_fetch
reads them. The tool itself is small — it just:

  1. Picks a backend via ``registry.select(prefer=provider)``, OR runs
     several backends in parallel and merges them via the
     ``combine.py`` aggregator (RRF / race).
  2. Delegates the actual search to the chosen backend(s).
  3. Formats results into a stable numbered-list string the agent can
     scan (title, URL, 1-2 sentence snippet).

16 backends register at import time, sorted by ``priority`` for the
auto-select path:

    100 tavily       95 exa        93 kagi       90 perplexity
     85 brave        83 youcom     82 serper     80 google
     75 firecrawl    70 searxng    65 minimax    60 moonshot
     55 ollama       35 jina       30 arxiv      10 duckduckgo

Provider backends live in ``web_search/providers/`` — each one is a
small dataclass with ``name / priority / requires_env / is_available /
search`` methods. Adding a new provider is a single file plus one
registry.register() call.
"""

from __future__ import annotations

from typing import Any

from openprogram.programs._helpers import is_available as _tool_is_available
from openprogram.programs._helpers import read_int_param, read_string_param
from openprogram.programs._runtime import ToolReturn, function
from . import providers as _  # registers builtins on import  # noqa: F401
from .registry import SearchResult, registry


NAME = "web_search"


def _build_description() -> str:
    return (
        "Search the web for ranked results (title, URL, snippet). "
        "Use web_fetch to read full pages. Omit provider to use the saved default "
        "or the highest-priority available backend. Explicit providers require "
        "their credentials to be configured. Use arxiv for academic papers. "
        "combine='rrf' merges timely results from multiple providers; "
        "combine='race' returns the first nonempty successful response. "
        "The providers array restricts aggregation to named backends."
    )


def _build_spec() -> dict:
    """Stable provider names; credential availability is checked at execution."""
    avail = [p.name for p in registry.all()]
    provider_schema = {
        "type": "string",
        "description": (
            "Force a specific backend (omit for auto-select)."
            + (" Use `arxiv` for academic / paper searches." if "arxiv" in avail else "")
        ),
    }
    if avail:
        provider_schema["enum"] = avail

    providers_schema = {
        "type": "array",
        "items": (
            {"type": "string", "enum": avail} if avail else {"type": "string"}
        ),
        "description": (
            "Explicit provider list for combine mode. Defaults to every "
            "available provider when `combine` is set and this is omitted."
        ),
    }

    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Natural-language is fine for "
                    "Tavily/Exa; keyword-style works best for DDG."
                ),
            },
            "num_results": {
                "type": "integer",
                "description": "Maximum number of results (default 8, typical cap 20).",
            },
            "provider": provider_schema,
            "combine": {
                "type": "string",
                "description": (
                    "Multi-provider blend strategy. 'rrf' runs the query "
                    "against several backends in parallel and merges via "
                    "Reciprocal Rank Fusion (best for high-stakes research). "
                    "'race' returns the first backend's results that arrive "
                    "(best when latency matters)."
                ),
                "enum": ["rrf", "race"],
            },
            "providers": providers_schema,
        },
        "required": ["query"],
    }


# Registration is credential-independent so newly configured keys work immediately.
DESCRIPTION = _build_description()
SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": _build_spec(),
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
    combine: str | None = None,
    providers: list | None = None,
    **kw: Any,
) -> str:
    if query is None:
        query = read_string_param(kw, "query", "q")
    if not query:
        return "Error: `query` is required."
    num_results = read_int_param(kw, "num_results", "numResults", default=num_results) or num_results
    num_results = max(1, min(int(num_results), 25))
    provider = read_string_param(kw, "provider", "backend", default=provider)
    combine = read_string_param(kw, "combine", "strategy", default=combine)

    # ``providers`` may arrive as a JSON array (from the LLM tool call)
    # or a comma-separated string (from CLI / legacy callers). Normalise
    # to ``list[str]``.
    if providers is None:
        providers = kw.get("providers")
    if isinstance(providers, str):
        providers = [p.strip() for p in providers.split(",") if p.strip()]

    # --- Combine path: run RRF / race across several providers ---------
    if combine:
        from .combine import combine_race, combine_rrf
        combine = combine.lower()
        if combine not in {"rrf", "race"}:
            return "Error: combine must be rrf or race."
        fn = combine_rrf if combine == "rrf" else combine_race
        try:
            merged, contributors = fn(
                query, num_results=num_results, providers=providers,
            )
        except LookupError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: combine ({combine}) failed: {type(e).__name__}: {e}"
        label = f"{combine}: {' + '.join(contributors) or '(none)'}"
        return _format(query, label, merged)

    # --- Single-provider path (legacy default) -------------------------
    # Caller didn't pin a backend → use the user's saved default if any,
    # otherwise fall through to priority-based auto-select.
    if not provider:
        try:
            from openprogram.setup import read_search_default_provider
            stored = read_search_default_provider()
            if stored and stored in {p.name for p in registry.available()}:
                provider = stored
        except Exception:
            pass

    try:
        backend = registry.select(prefer=provider)
    except LookupError as e:
        return f"Error: {e}"

    try:
        results = backend.search(query, num_results=num_results)
    except Exception as e:
        return f"Error: {backend.name} search failed: {type(e).__name__}: {e}"

    return _format(query, backend.name, results)



def _execute_agent_tool(**kwargs) -> ToolReturn:
    text = execute(**kwargs)
    return ToolReturn(text=text, is_error=text.startswith("Error:"))


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core', 'research'],
    check_fn=_tool_check_fn,
)(_execute_agent_tool)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
