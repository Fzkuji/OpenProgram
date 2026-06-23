"""Load and query per-provider cache.json specs.

Mirrors ``thinking_spec.py``. Each provider may declare a ``cache.json``
saying how its API handles prompt caching. Providers without one fall back
to ``none`` (no caching control) — the same self-contained, add-a-file
model as thinking.json.

  get_cache_spec(provider_id) -> the parsed dict for one provider

Cache modes (the ``mode`` field):
  "explicit"     — caller/provider puts explicit breakpoints in the request
                   (Anthropic ``cache_control`` blocks, Bedrock ``cachePoint``).
  "auto"         — provider does automatic prefix caching; we only pass an
                   optional cache key (OpenAI ``prompt_cache_key``).
  "out_of_band"  — caching is a separate API call (Gemini cachedContent);
                   not driven from the request body.
  "none"         — no caching control (fallback for unknown providers).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROVIDERS_DIR = Path(__file__).parent

# Providers that share another provider's cache config (same API).
_CACHE_ALIASES: dict[str, str] = {
    "claude-code": "anthropic",
}

# Providers without a cache.json get no caching control. Most OpenAI-compatible
# community endpoints (groq, mistral, openrouter, …) either don't cache or do it
# transparently with no per-request knob, so "none" is the safe default.
_NONE_FALLBACK: dict[str, Any] = {"mode": "none", "_fallback": True}


@lru_cache(maxsize=None)
def get_cache_spec(provider_id: str) -> dict[str, Any]:
    """Load cache.json for a provider.

    Returns the ``none`` fallback when no cache.json exists or provider_id is
    empty (e.g. the current provider couldn't be resolved) — never raises, so
    callers stay on the happy path.
    """
    if not provider_id:
        return _NONE_FALLBACK
    resolved = _CACHE_ALIASES.get(provider_id, provider_id)
    for dir_name in (resolved, resolved.replace("-", "_")):
        path = _PROVIDERS_DIR / dir_name / "cache.json"
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return _NONE_FALLBACK
    return _NONE_FALLBACK


def cache_mode(provider_id: str) -> str:
    """The provider's cache mode: explicit / auto / out_of_band / none."""
    return get_cache_spec(provider_id).get("mode", "none")


def ttl_for_retention(provider_id: str, retention: str | None) -> str | None:
    """Map a framework retention level ("short"/"long"/"none") to the
    provider's API TTL value, or None when no explicit TTL applies.

    Used by explicit-mode providers; reads ``retention_ttl_map`` from the spec.
    """
    if retention == "none":
        return None
    spec = get_cache_spec(provider_id)
    return (spec.get("retention_ttl_map") or {}).get(retention or "short")


def cache_key_param(provider_id: str) -> str | None:
    """The request param name for auto-mode prefix-cache keys (e.g.
    ``prompt_cache_key``), or None when the provider takes no key."""
    return get_cache_spec(provider_id).get("cache_key_param")


def invalidate_cache() -> None:
    """Clear the cached specs (for tests or hot reload)."""
    get_cache_spec.cache_clear()


__all__ = [
    "get_cache_spec",
    "cache_mode",
    "ttl_for_retention",
    "cache_key_param",
    "invalidate_cache",
]
