"""Builtin web_search providers.

Importing this package registers every first-party backend (Tavily, Exa,
DuckDuckGo). Third parties can register additional providers by calling
``registry.register(...)`` after importing.
"""

from __future__ import annotations

from ..registry import registry
from .duckduckgo import DuckDuckGoProvider
from .exa import ExaProvider
from .tavily import TavilyProvider


def _register_builtins() -> None:
    # Higher priority = tried first when auto-selecting. Tavily ranks
    # highest because it's the search backend most tuned for LLM agents;
    # Exa second because its neural search catches things keyword
    # engines miss; DDG last as the zero-key fallback.
    registry.register(TavilyProvider())
    registry.register(ExaProvider())
    registry.register(DuckDuckGoProvider())


_register_builtins()


__all__ = ["DuckDuckGoProvider", "ExaProvider", "TavilyProvider"]
