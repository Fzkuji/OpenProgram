"""Provider-level api/base_url from providers/<p>/provider.json — no ENABLED_MODELS,
no webui import. Breaks the providers<->webui circular dep for the derivation
helpers (_default_api_for / _resolve_base_url)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).parent


def _provider_dir(provider_id: str) -> Path | None:
    # Same hyphen->underscore resolution as provider_models._provider_dir, but
    # WITHOUT importing webui (that would re-create the cycle this module breaks).
    # Read-only: never creates a dir.
    for name in (provider_id, provider_id.replace("-", "_")):
        d = _ROOT / name
        if (d / "provider.json").is_file():
            return d
    return None


def _endpoints(provider_id: str) -> dict:
    d = _provider_dir(provider_id)
    if d is None:
        return {}
    try:
        return (json.loads((d / "provider.json").read_text(encoding="utf-8")).get("endpoints") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _models_dev_base_url(provider_id: str) -> str | None:
    """models.dev catalogue base_url for a community provider, or None.

    Lazy + guarded: the webui listing layer owns the models.dev cache, and
    importing it here must never break the providers layer at load time."""
    try:
        from openprogram.webui._model_listing.providers import _default_base_url_for
        return _default_base_url_for(provider_id)
    except Exception:
        return None


def resolved_endpoints(provider_id: str) -> dict:
    """Endpoint map for a provider, resolving the empty-dir gap.

    Community / token-plan providers (``minimax-cn-coding-plan``) and
    alias-only providers (``chatgpt-subscription`` → ``openai-codex``) ship
    an EMPTY provider dir — no ``provider.json`` — so their own endpoints are
    ``{}`` and rows built off them get a hostless base_url. Fill order:

      1. The provider's own ``provider.json`` endpoints (unchanged path).
      2. Its alias target's endpoints (``chatgpt-subscription`` inherits
         ``openai-codex``'s api + base_url, so the row routes to the codex
         transport that resolves credentials under the canonical id).
      3. A synthetic ``{"default": {api, base_url}}`` from models.dev's
         base_url — with ``anthropic-messages`` when that base is an
         ``/anthropic`` endpoint, else ``openai-completions``.
    """
    eps = _endpoints(provider_id)
    if eps:
        return eps
    try:
        from openprogram.auth.aliases import resolve as _canon
        canon = _canon(provider_id)
    except Exception:
        canon = provider_id
    if canon != provider_id:
        eps = _endpoints(canon)
        if eps:
            return eps
    # A ``-coding-plan`` token-plan provider shares its region sibling's
    # wire (same account, same endpoint) — e.g. ``minimax-cn-coding-plan``
    # → ``minimax-cn``. Derive OFFLINE from the sibling's provider.json
    # before touching models.dev: the models.dev catalogue is an in-memory,
    # network-fetched, TTL cache that is EMPTY at cold import (and the
    # registry snapshots at import), so a network-only path silently yields
    # a hostless base_url in a fresh process.
    if provider_id.endswith("-coding-plan"):
        eps = _endpoints(provider_id[: -len("-coding-plan")])
        if eps:
            return eps
    base = (_models_dev_base_url(provider_id) or "").rstrip("/")
    if not base:
        return {}
    api = "anthropic-messages" if (
        base.endswith("/anthropic") or base.endswith("/anthropic/v1")
    ) else "openai-completions"
    return {"default": {"api": api, "base_url": base}}


def provider_endpoints(provider_id: str) -> dict:
    """Full endpoint map {name: {api, base_url}}, resolving empty provider
    dirs via alias / models.dev (see :func:`resolved_endpoints`)."""
    return resolved_endpoints(provider_id)


def provider_apis(provider_id: str) -> set[str]:
    return {e.get("api") for e in _endpoints(provider_id).values() if e.get("api")}


def provider_base_url(provider_id: str) -> str | None:
    eps = _endpoints(provider_id)
    ep = eps.get("default") or (next(iter(eps.values())) if eps else None)
    return ep.get("base_url") if ep else None
