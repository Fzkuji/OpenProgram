"""Web-search provider metadata catalog.

Single source of truth for human-facing info about each backend:
description, pricing tier, where to sign up, link to OpenClaw setup
docs, and a 2-5 step "how to get this working" recipe. Consumed by:

  * ``openprogram/webui/routes/providers.py`` — surfaces the metadata
    over ``/api/search-providers/list`` so the React settings page can
    render a rich detail panel.
  * ``openprogram/_setup_sections/sections.py`` (TUI picker) — prints
    ``setup_steps`` and ``signup_url`` after the user picks an
    unconfigured backend, so the CLI flow tells you where to go next
    without trying to handle key entry itself.
  * ``web/public/js/shared/ui.js`` (legacy plus-menu) — pulls ``tier``
    into the Web Search chip tooltip ("Web Search · Brave · Free 2000
    q/mo").

Why a separate module:

  * The provider classes themselves only care about runtime config
    (env vars, endpoint URLs, HTTP transport). Stuffing freeform setup
    prose into each provider's docstring made it hard to surface
    consistently across three UI surfaces; centralising here means the
    UIs can iterate independently from the search runtime.
  * Entries can exist for providers that aren't registered yet
    (moonshot / kimi / ollama-search). UI shows them as "not
    configured" with a setup hint; flipping a switch later just means
    importing the corresponding module.

URLs are sourced verbatim from the OpenClaw docs at
``references/openclaw/docs/tools/{name}-search.md`` — do **not**
fabricate signup URLs; if the upstream doc didn't include one, leave
the field as ``None`` and the UI hides the row gracefully.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ProviderInfo:
    """Display-only metadata for a search backend.

    Runtime behaviour (env vars, transport, fallback order) stays on
    the provider class. This record is pure documentation; the API
    layer flattens it with ``asdict()`` for JSON-serialisation.
    """

    name: str
    description: str
    # Short pricing/availability tier blurb. Shown as a chip in the UI
    # ("Free 1000 q/mo", "Pay-as-you-go", "Self-hosted", etc.). Keep
    # under ~20 chars so it fits in the legacy tooltip.
    tier: str = ""
    # Where the user goes to get an API key / set up the backend. Set
    # to None when the backend doesn't need credentials (DDG, SearXNG).
    signup_url: str | None = None
    # Link to the canonical setup documentation. Right now this points
    # at openclaw's docs since they cover every backend; once we have
    # our own docs site we'd swap individual entries here.
    docs_url: str | None = None
    # 2-5 short imperative-form steps for the "how do I enable this?"
    # block. UI renders as an <ol>; TUI prints as numbered lines.
    setup_steps: list[str] = field(default_factory=list)


# Provider id -> ProviderInfo. Ids match registry names (lowercased).
# Entries for not-yet-registered providers (kimi/moonshot, ollama) are
# included so the catalog stays the single source of truth — the
# settings UI just won't render rows for ids that aren't in
# /api/search-providers/list. The TUI picker is similar.
_CATALOG: dict[str, ProviderInfo] = {
    "tavily": ProviderInfo(
        name="Tavily",
        description=(
            "LLM-tuned search API. Snippets are pre-summarised for "
            "agent consumption, so follow-up web_fetch calls are usually "
            "unnecessary for short Q&A."
        ),
        tier="Free 1000 q/mo",
        signup_url="https://tavily.com/",
        docs_url=None,
        setup_steps=[
            "Sign up at tavily.com",
            "Generate an API key from the dashboard",
            "Paste it into the API key field above",
        ],
    ),
    "exa": ProviderInfo(
        name="Exa",
        description=(
            "Neural search engine — finds semantically related pages "
            "that keyword engines miss. Supports content extraction "
            "(highlights, full text, AI summary) in a single call."
        ),
        tier="Pay-as-you-go",
        signup_url="https://exa.ai/",
        docs_url=None,
        setup_steps=[
            "Sign up at exa.ai",
            "Generate an API key from the dashboard",
            "Paste it into the API key field above",
        ],
    ),
    "perplexity": ProviderInfo(
        name="Perplexity",
        description=(
            "Sonar API — returns an LLM-written answer with citations "
            "instead of a raw hit list. Good for one-shot Q&A; not the "
            "right choice when you want browse-style result rows."
        ),
        tier="Pay-as-you-go",
        signup_url="https://www.perplexity.ai/settings/api",
        docs_url=None,
        setup_steps=[
            "Create a Perplexity account",
            "Generate an API key at perplexity.ai/settings/api",
            "Paste it into the API key field above",
        ],
    ),
    "brave": ProviderInfo(
        name="Brave",
        description=(
            "Independent index (not bing/google rerank), privacy-first. "
            "Includes $5/mo free credit covering ~1000 queries on the "
            "Search plan."
        ),
        tier="$5/mo free credit",
        signup_url="https://brave.com/search/api/",
        docs_url=None,
        setup_steps=[
            "Sign up at brave.com/search/api",
            "Pick the Search plan and generate an API key",
            "Set a monthly usage cap in the dashboard to avoid surprise charges",
            "Paste the key into the API key field above",
        ],
    ),
    "google": ProviderInfo(
        name="Google PSE",
        description=(
            "Real Google index via Programmable Search Engine. Needs "
            "both an API key AND a custom search engine id (CX). Free "
            "tier: 100 queries/day."
        ),
        tier="Free 100 q/day",
        signup_url="https://programmablesearchengine.google.com/",
        docs_url="https://developers.google.com/custom-search/v1/overview",
        setup_steps=[
            "Create a Programmable Search Engine at programmablesearchengine.google.com",
            "Copy the search engine id (CX) and save it as GOOGLE_PSE_CX",
            "Enable Custom Search API in Google Cloud Console",
            "Create an API key and save it as GOOGLE_PSE_API_KEY",
            "Both env vars are required — the backend is inactive until both are set",
        ],
    ),
    "firecrawl": ProviderInfo(
        name="Firecrawl",
        description=(
            "Search + full-page content in one call. Handles JS-rendered "
            "and bot-protected pages, so an agent rarely needs a "
            "follow-up web_fetch."
        ),
        tier="Free 500 credits/mo",
        signup_url="https://www.firecrawl.dev/",
        docs_url=None,
        setup_steps=[
            "Sign up at firecrawl.dev",
            "Generate an API key from the dashboard",
            "Paste it into the API key field above",
        ],
    ),
    "searxng": ProviderInfo(
        name="SearXNG",
        description=(
            "Self-hosted meta search — aggregates Google/Bing/DDG without "
            "sending your queries to them directly. Free and unlimited "
            "once you run an instance."
        ),
        tier="Self-hosted",
        # No central signup — it's open source you host yourself.
        signup_url=None,
        docs_url=None,
        setup_steps=[
            "Run a SearXNG instance: docker run -d -p 8888:8080 searxng/searxng",
            "Enable the JSON format in the instance's settings.yml",
            "Set SEARXNG_URL to the instance URL (e.g. http://localhost:8888)",
        ],
    ),
    "duckduckgo": ProviderInfo(
        name="DuckDuckGo",
        description=(
            "Zero-key public fallback. Scrapes DuckDuckGo's non-JS "
            "search pages, so expect occasional CAPTCHA / HTML-change "
            "breakage. Use it as a backup, not a primary backend."
        ),
        tier="No key needed",
        signup_url=None,
        docs_url=None,
        setup_steps=[
            "No setup required — used automatically when no other backend is configured",
        ],
    ),
    "minimax": ProviderInfo(
        name="MiniMax",
        description=(
            "Structured results via MiniMax's Coding Plan search API. "
            "Auto-selects the global (api.minimax.io) or CN "
            "(api.minimaxi.com) endpoint based on MINIMAX_API_HOST."
        ),
        tier="Coding Plan subscription",
        signup_url=(
            "https://platform.minimax.io/user-center/basic-information/interface-key"
        ),
        docs_url=None,
        setup_steps=[
            "Subscribe to a MiniMax Coding Plan",
            "Copy your Coding Plan key from the dashboard",
            "Set MINIMAX_CODE_PLAN_KEY (or MINIMAX_CODING_API_KEY / MINIMAX_API_KEY)",
            "For CN host: set MINIMAX_API_HOST=https://api.minimaxi.com",
        ],
    ),
    "moonshot": ProviderInfo(
        name="Moonshot Kimi",
        description=(
            "Moonshot/Kimi web search — synthesises an answer with "
            "citations using kimi-k2.5. Returns a single answer row, "
            "not an N-result list."
        ),
        tier="Pay-as-you-go",
        signup_url="https://platform.moonshot.cn/",
        docs_url=None,
        setup_steps=[
            "Create an account at platform.moonshot.cn (or platform.moonshot.ai)",
            "Generate an API key from the dashboard",
            "Set KIMI_API_KEY or MOONSHOT_API_KEY in the environment",
            "International keys use https://api.moonshot.ai/v1; CN keys use https://api.moonshot.cn/v1",
        ],
    ),
    "ollama": ProviderInfo(
        name="Ollama Web Search",
        description=(
            "Web search via Ollama's experimental /api/experimental/"
            "web_search endpoint. No web-search-specific key needed if "
            "your Ollama host is reachable and signed in."
        ),
        tier="Self-hosted",
        signup_url="https://ollama.com/",
        docs_url=None,
        setup_steps=[
            "Install and start Ollama (ollama.com/download)",
            "Run `ollama signin` to authenticate",
            "Make sure the Ollama host is reachable from OpenProgram",
            "Pick `ollama` as the default search backend in Settings",
        ],
    ),
}


def get(provider_id: str) -> ProviderInfo | None:
    """Return metadata for the given provider id, or None if unknown.

    Lookup is lowercase-id keyed; callers can pass either ``"tavily"``
    or ``"Tavily"`` since registry names are always lowercase but UI
    display names aren't.
    """

    if not provider_id:
        return None
    return _CATALOG.get(str(provider_id).strip().lower())


def get_dict(provider_id: str) -> dict | None:
    """Same as ``get`` but returns a JSON-serializable dict.

    Used by the FastAPI route so we don't expose the dataclass on the
    wire — easier to evolve the wire format without touching the
    catalog shape.
    """

    info = get(provider_id)
    if info is None:
        return None
    return asdict(info)


def all_dicts() -> dict[str, dict]:
    """Return every catalog entry as a plain dict, keyed by provider id.

    Handy for batch endpoints. The API layer prefers per-row lookup
    via ``get_dict`` so it doesn't expose entries for unregistered
    providers, but tooling and tests can use this to dump the full
    catalog.
    """

    return {pid: asdict(info) for pid, info in _CATALOG.items()}


__all__ = ["ProviderInfo", "get", "get_dict", "all_dicts"]
