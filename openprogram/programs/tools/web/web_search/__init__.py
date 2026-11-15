"""web_search tool — re-exports TOOL record + provider registry."""

from .registry import SearchResult, WebSearchProvider, registry
from .web_search import DESCRIPTION, NAME, SPEC, execute

__all__ = [
    "NAME",
    "SPEC",
    "execute",
    "DESCRIPTION",
    "SearchResult",
    "WebSearchProvider",
    "registry",
]
