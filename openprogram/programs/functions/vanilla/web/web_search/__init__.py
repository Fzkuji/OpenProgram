"""web_search tool — re-exports TOOL record + provider registry."""

from .registry import SearchResult, WebSearchProvider, registry
from .web_search import DESCRIPTION, NAME, SPEC, _tool_check_fn, execute

__all__ = [
    "NAME",
    "SPEC",
    "execute",
    "DESCRIPTION",
    "SearchResult",
    "WebSearchProvider",
    "registry",
]
