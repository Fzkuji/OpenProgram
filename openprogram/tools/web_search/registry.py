"""web_search provider registry + base class.

Defines the contract every search backend implements and exposes a
single module-level ``registry`` instance the tool's execute() uses to
pick a backend at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .._providers import ProviderRegistry


@dataclass
class SearchResult:
    """One hit in a web search response — provider-agnostic shape."""

    title: str
    url: str
    snippet: str = ""
    # Optional provider-specific extras (score, published date, etc.) —
    # kept loose so we don't have to update the dataclass for every new
    # backend. Callers who care inspect ``extras``.
    extras: dict = field(default_factory=dict)


@runtime_checkable
class WebSearchProvider(Protocol):
    """A search backend. Registered instances must implement ``search``."""

    name: str
    priority: int
    requires_env: list[str]

    def is_available(self) -> bool: ...
    def search(self, query: str, *, num_results: int = 8) -> list[SearchResult]: ...


# Shared registry — populated by `providers/__init__.py` on import.
registry: ProviderRegistry[WebSearchProvider] = ProviderRegistry[WebSearchProvider]("web_search")


__all__ = ["SearchResult", "WebSearchProvider", "registry"]
