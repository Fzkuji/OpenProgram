"""web_search tool — re-exports TOOL record + provider registry."""

from .registry import SearchResult, WebSearchProvider, registry
from .web_search import DESCRIPTION, NAME, SPEC, _tool_check_fn, execute

TOOL = {
    "spec": SPEC,
    "execute": execute,
    # Gate the tool on "at least one backend is configured" so the LLM
    # doesn't waste a call when no API keys are set and DDG isn't
    # installed either. Helpful error message surfaces via .select().
    "check_fn": _tool_check_fn,
    "max_result_size_chars": 20_000,
}

__all__ = [
    "NAME",
    "SPEC",
    "TOOL",
    "execute",
    "DESCRIPTION",
    "SearchResult",
    "WebSearchProvider",
    "registry",
]
