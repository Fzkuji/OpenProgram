"""Builtin web_search providers.

Importing this package registers every first-party backend.
Third parties can register additional providers by calling
``registry.register(...)`` after importing.
"""

from __future__ import annotations

from ..registry import registry
from .arxiv import ArxivProvider
from .brave import BraveProvider
from .duckduckgo import DuckDuckGoProvider
from .exa import ExaProvider
from .firecrawl import FirecrawlProvider
from .google import GoogleProvider
from .jina import JinaProvider
from .kagi import KagiProvider
from .minimax import MiniMaxProvider
from .moonshot import MoonshotProvider
from .ollama import OllamaProvider
from .perplexity import PerplexityProvider
from .searxng import SearxngProvider
from .serper import SerperProvider
from .tavily import TavilyProvider
from .youcom import YouComProvider


def _register_builtins() -> None:
    # Higher priority = tried first when auto-selecting. Ordering:
    #   100 tavily      — LLM-tuned snippets, fewest follow-up fetches needed
    #    95 exa         — neural search, catches semantically related pages
    #    93 kagi        — paid premium search, T-Rank re-ranking (when key set)
    #    90 perplexity  — answer-style with citations, good for one-shot Q&A
    #    85 brave       — independent index, generous free tier
    #    83 youcom      — agent-friendly snippets, sentence-level extracts
    #    82 serper      — cheap Google SERP via serper.dev
    #    80 google      — real Google results via Programmable Search Engine
    #    75 firecrawl   — SERP + page content, no second fetch needed
    #    70 searxng     — self-hosted meta search, privacy-first
    #    65 minimax     — Coding Plan search API, structured snippets
    #    60 moonshot    — Kimi $web_search tool-call, AI-synth answers + citations
    #    55 ollama      — local/cloud Ollama experimental web search
    #    35 jina        — markdown-shaped snippets, unauthed fallback
    #    30 arxiv       — academic papers; only useful for research queries
    #    10 duckduckgo  — zero-key public fallback
    # Ordering matters only for auto-select; explicit ``prefer=`` overrides.
    registry.register(TavilyProvider())
    registry.register(ExaProvider())
    registry.register(KagiProvider())
    registry.register(PerplexityProvider())
    registry.register(BraveProvider())
    registry.register(YouComProvider())
    registry.register(SerperProvider())
    registry.register(GoogleProvider())
    registry.register(FirecrawlProvider())
    registry.register(SearxngProvider())
    registry.register(MiniMaxProvider())
    registry.register(MoonshotProvider())
    registry.register(OllamaProvider())
    registry.register(JinaProvider())
    registry.register(ArxivProvider())
    registry.register(DuckDuckGoProvider())


_register_builtins()


__all__ = [
    "ArxivProvider",
    "BraveProvider",
    "DuckDuckGoProvider",
    "ExaProvider",
    "FirecrawlProvider",
    "GoogleProvider",
    "JinaProvider",
    "KagiProvider",
    "MiniMaxProvider",
    "MoonshotProvider",
    "OllamaProvider",
    "PerplexityProvider",
    "SearxngProvider",
    "SerperProvider",
    "TavilyProvider",
    "YouComProvider",
]
