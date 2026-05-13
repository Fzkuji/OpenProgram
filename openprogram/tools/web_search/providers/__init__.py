"""Builtin web_search providers.

Importing this package registers every first-party backend (Tavily,
Perplexity, Brave, Google PSE, Exa, Firecrawl, SearXNG, DuckDuckGo).
Third parties can register additional providers by calling
``registry.register(...)`` after importing.
"""

from __future__ import annotations

from ..registry import registry
from .brave import BraveProvider
from .duckduckgo import DuckDuckGoProvider
from .exa import ExaProvider
from .firecrawl import FirecrawlProvider
from .google import GoogleProvider
from .minimax import MiniMaxProvider
from .moonshot import MoonshotProvider
from .ollama import OllamaProvider
from .perplexity import PerplexityProvider
from .searxng import SearxngProvider
from .tavily import TavilyProvider


def _register_builtins() -> None:
    # Higher priority = tried first when auto-selecting. Ordering:
    #   100 tavily      — LLM-tuned snippets, fewest follow-up fetches needed
    #    95 exa         — neural search, catches semantically related pages
    #    90 perplexity  — answer-style with citations, good for one-shot Q&A
    #    85 brave       — independent index, generous free tier
    #    80 google      — real Google results via Programmable Search Engine
    #    75 firecrawl   — SERP + page content, no second fetch needed
    #    70 searxng     — self-hosted meta search, privacy-first
    #    65 minimax     — Coding Plan search API, structured snippets
    #    60 moonshot    — Kimi $web_search tool-call, AI-synth answers + citations
    #    55 ollama      — local/cloud Ollama experimental web search
    #    10 duckduckgo  — zero-key public fallback
    # Ordering matters only for auto-select; explicit ``prefer=`` overrides.
    registry.register(TavilyProvider())
    registry.register(ExaProvider())
    registry.register(PerplexityProvider())
    registry.register(BraveProvider())
    registry.register(GoogleProvider())
    registry.register(FirecrawlProvider())
    registry.register(SearxngProvider())
    registry.register(MiniMaxProvider())
    registry.register(MoonshotProvider())
    registry.register(OllamaProvider())
    registry.register(DuckDuckGoProvider())


_register_builtins()


__all__ = [
    "BraveProvider",
    "DuckDuckGoProvider",
    "ExaProvider",
    "FirecrawlProvider",
    "GoogleProvider",
    "MiniMaxProvider",
    "MoonshotProvider",
    "OllamaProvider",
    "PerplexityProvider",
    "SearxngProvider",
    "TavilyProvider",
]
