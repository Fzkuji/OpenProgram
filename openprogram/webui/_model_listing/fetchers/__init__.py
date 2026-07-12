"""Model-list dispatcher.

A provider that lists models in a way the generic OpenAI-compatible
``/v1/models`` fetcher can't handle ships a ``list_models.py`` in its own
directory (``openprogram/providers/<name>/list_models.py``) exposing:

    def fetch(provider_id: str, timeout: float) -> list[dict] | {"error": ...}

The dispatcher loads that by directory name — the same convention
``probe_thinking.probe()`` uses — so adding a provider needs no edit here; it
just drops a file in its own directory. Providers whose ``/v1/models`` is
standard don't ship the file and use the generic ``_fetch_openai_compat``
(kept here because it belongs to no single provider). See
``docs/design/providers/models/models.md`` §4.1.

Each ``fetch`` returns either a list of model dicts (success) or a dict with an
``"error"`` key (failure, human-readable). ``fetch_and_normalize`` then:

1. Picks the source (per-provider ``list_models.fetch`` → anthropic-wire route
   → generic ``_fetch_openai_compat`` → custom-provider → error).
2. Normalises every response into one entry shape — pulling
   ``context_window`` / ``max_tokens`` from the several spellings upstreams
   use, lifting cost hints, carrying a fetcher's fast/thinking through, and
   deriving thinking via ``thinking_spec.derive_thinking_fields``.
3. (Refresh path) rotates the enabled spec rows via storage.
"""
from __future__ import annotations

from typing import Any

# Generic OpenAI-compatible /v1/models fetcher — the fallback for every
# provider that DOESN'T ship its own list_models.py. It belongs to no single
# provider, so it stays here rather than in a provider directory.
from .openai_compat import _fetch_openai_compat


# Providers whose model source is loaded by directory-name convention from
# ``providers/<dir>/list_models.py::fetch``. claude-code has no directory of its
# own (it rides the anthropic runtime), so it's mapped to anthropic's module
# explicitly; every other entry resolves to its own provider directory.
_LIST_MODELS_MODULE_OVERRIDES = {
    "claude-code": "anthropic",
}


def _load_fetcher(provider_id: str) -> Any:
    """Load ``providers.<dir>.list_models.fetch`` for a provider, or ``None``
    when it ships no such module (→ dispatcher falls back to the generic
    fetcher). Mirrors ``_load_probe``: directory name from the provider id,
    convention module ``list_models``, convention function ``fetch``."""
    dir_name = _LIST_MODELS_MODULE_OVERRIDES.get(
        provider_id, provider_id.replace("-", "_")
    )
    try:
        mod = __import__(
            f"openprogram.providers.{dir_name}.list_models",
            fromlist=["fetch"],
        )
        return mod.fetch
    except (ImportError, AttributeError):
        return None


# Cache probed reasoning results per Fetch (avoid calling probe() per model)
_probe_cache: dict[str, dict] = {}


def _load_probe(provider_id: str) -> Any:
    """Load and cache the probe_thinking.probe() for a provider."""
    if provider_id in _probe_cache:
        return lambda: _probe_cache[provider_id]
    try:
        dir_name = provider_id.replace("-", "_")
        mod = __import__(
            f"openprogram.providers.{dir_name}.probe_thinking",
            fromlist=["probe"],
        )
        results = mod.probe()
        _probe_cache[provider_id] = results
        return lambda: results
    except (ImportError, AttributeError):
        _probe_cache[provider_id] = {}
        return None


def fetch_and_normalize(provider_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Dispatch to a provider-specific fetcher and normalise the result
    into the catalog row shape — WITHOUT any persistence.

    This is the pure "hit the official API and enrich" half of the old
    Fetch flow, reused by the live-browse path (``listing.list_models_for_provider``)
    so opening the settings page queries the provider live instead of
    reading a persisted snapshot.

    Returns ``{"models": [...]}`` on success or ``{"error": "..."}`` on
    failure. Never persists.
    """
    from openprogram.providers.thinking_spec import derive_thinking_fields

    from ..providers import _FETCH_MODELS_PROVIDERS, _default_api_for, _label
    from ..sources import enrich as _enrich_from_community
    from ..storage import _is_custom_provider

    fetcher = _load_fetcher(provider_id)
    # Providers that speak the Anthropic Messages wire format (minimax,
    # minimax-cn, …) expose Anthropic's GET /v1/models with x-api-key —
    # the OpenAI-compatible GET /models 404s on their /anthropic host.
    # Route them to the (now base_url-aware) Anthropic fetcher before the
    # OpenAI-compat fallback. anthropic's list_models.fetch is base_url-aware.
    if fetcher is None and _default_api_for(provider_id) == "anthropic-messages":
        fetcher = _load_fetcher("anthropic")
    if fetcher is None and provider_id in _FETCH_MODELS_PROVIDERS:
        fetcher = _fetch_openai_compat
    # Custom (user-added) providers: config-only OpenAI-compatible endpoints
    # with no dir / models.dev entry. Their /models is the standard
    # OpenAI-compatible list, so route them through the generic fetcher — it
    # resolves base_url + key from config/AuthStore. On failure the generic
    # fetcher returns {"error": ...}, which _browse_models degrades to empty
    # (never caches a failure as success).
    if fetcher is None and _is_custom_provider(provider_id):
        fetcher = _fetch_openai_compat
    if fetcher is None:
        return {"error": (
            f"{_label(provider_id)} has no list-models API available. "
            "Models are curated manually for this provider."
        )}

    raw = fetcher(provider_id, timeout)
    if isinstance(raw, dict) and "error" in raw:
        return raw
    items = raw if isinstance(raw, list) else []
    if not items:
        return {"error": "No models returned"}

    models: list[dict[str, Any]] = []
    for it in items:
        if isinstance(it, str):
            models.append({"id": it, "name": it})
            continue
        if not isinstance(it, dict):
            continue
        mid = it.get("id") or it.get("name")
        if not mid:
            continue
        # OpenRouter and friends include extras; keep id+name and
        # basics. Anything missing here gets filled in below from the
        # ``sources.enrich`` community catalogue.
        entry: dict[str, Any] = {
            "id": mid,
            "name": it.get("name") or mid,
        }
        ctx = it.get("context_length") or it.get("context_window") or it.get("contextWindow")
        if ctx:
            try: entry["context_window"] = int(ctx)
            except Exception: pass
        # Some fetchers (DeepSeek, custom enrichers) supply a max-tokens
        # cap they know about — surface it instead of zeroing out the
        # column. OpenRouter / OpenAI ``/v1/models`` don't include this
        # natively so the conditional makes it a no-op there.
        mtok = it.get("max_tokens") or it.get("maxTokens") or it.get("output_token_limit")
        if mtok:
            try: entry["max_tokens"] = int(mtok)
            except Exception: pass
        if it.get("vision") or "vision" in str(it.get("architecture", {})).lower():
            entry["vision"] = True
        reasoning_hint = bool(it.get("reasoning"))
        if reasoning_hint:
            entry["reasoning"] = True
        # Pass through cost hints when the fetcher computed them
        # (DeepSeek enricher does; vanilla OpenAI-compatible fetchers
        # don't). The catalog UI renders these inline.
        for cost_key in ("input_cost", "output_cost", "cache_read_cost"):
            if cost_key in it:
                entry[cost_key] = it[cost_key]
        # Capability fields a fetcher resolved from the provider's own
        # account/models API are authoritative — carry them through so the
        # models.dev enrichment below can't overwrite them. Codex's endpoint
        # gives the real fast/thinking picker per model; models.dev only has
        # the public-API-platform guess. ``setdefault`` in enrich already
        # respects anything present here.
        for cap_key in ("fast", "thinking_levels", "default_thinking_level"):
            if cap_key in it:
                entry[cap_key] = it[cap_key]
        # Community enrichment: ask models.dev (and any other source
        # in ``sources._SOURCES``) for context window, output cap,
        # pricing, modalities, reasoning flag. Provider APIs like
        # DeepSeek's only return ``{id, owned_by}`` so without this
        # step their rows would land in the UI with all the numeric
        # columns at 0 — exactly the "什么都没有吗?" report we got
        # from the original DeepSeek launch.
        #
        # ``setdefault`` semantics: per-fetcher hints (e.g. a richer
        # ``/v1/models`` response from OpenRouter) win when both
        # sources have an opinion. We only fill in what the fetcher
        # didn't already populate.
        for k, v in _enrich_from_community(provider_id, mid).items():
            entry.setdefault(k, v)
        # Auto-detect reasoning capability from provider's probe module
        # if not already set by the fetcher or enrichment.
        if "reasoning" not in entry:
            try:
                _probe = _load_probe(provider_id)
                if _probe:
                    _probe_results = _probe()
                    _probe_info = _probe_results.get(mid, {})
                    if _probe_info.get("reasoning"):
                        entry["reasoning"] = True
            except Exception:
                pass
        reasoning_hint = bool(entry.get("reasoning") or reasoning_hint)
        # Thinking capability: if the fetcher already extracted levels
        # from the API (e.g. Anthropic capabilities), keep them as
        # authoritative. Otherwise derive from thinking.json / catalog.
        if not entry.get("thinking_levels"):
            levels, default_lv, variant = derive_thinking_fields(
                provider_id, mid, reasoning_hint
            )
            if levels:
                entry["thinking_levels"] = levels
                if default_lv:
                    entry["default_thinking_level"] = default_lv
                if variant:
                    entry["thinking_variant"] = variant
        models.append(entry)

    return {"models": models}


def fetch_models_remote(provider_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Refresh entry point (the "Fetch Models" button).

    Semantics (per the enabled-models redesign): NO file persistence.
    Instead:

      1. force-refresh the live-browse cache for this provider so the
         settings page shows the freshest official-API list, and
      2. for every currently-ENABLED model of this provider that appears
         in that fresh browse result, overwrite its stored spec row in
         config (via the Task-2 spec-row machinery) so a stale enabled
         spec heals. Enabled models absent from the fresh result keep
         their stored spec (an upstream blip must not drop the user's
         selection).

    Returns ``{"fetched": N, "refreshed": [...], ...}`` on success,
    ``{"error": "..."}`` on failure.
    """
    from ..listing import list_models_for_provider
    from ..storage import (
        _cache_lock,
        _read_providers_cfg,
        _upsert_spec_row,
        _write_providers_cfg,
    )

    # Force-refresh browse (bypasses the short-TTL cache). Also surfaces the
    # official-API error to the caller if the fetch failed outright.
    rows = list_models_for_provider(provider_id, force_refresh=True)
    by_id = {r.get("id"): r for r in rows if r.get("id")}

    refreshed: list[str] = []
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        enabled_ids = [
            r.get("id") for r in (pcfg.get("models") or []) if r.get("id")
        ] or list(pcfg.get("enabled_models") or [])
        changed = False
        for mid in enabled_ids:
            fresh = by_id.get(mid)
            if not fresh:
                continue  # absent upstream → keep the stored spec
            spec = {k: v for k, v in fresh.items() if k != "enabled"}
            _upsert_spec_row(pcfg, spec)
            refreshed.append(mid)
            changed = True
        if changed:
            _write_providers_cfg(cfg)

    if changed:
        # Config spec rows changed → rebuild the runtime registry in place
        # so the chat picker / runtime see the healed specs immediately.
        from openprogram.providers import enabled_models as _mg
        _mg.reload()

    return {
        "provider": provider_id,
        "fetched": len(rows),
        "refreshed": refreshed,
    }


__all__ = ["fetch_models_remote", "fetch_and_normalize", "_load_fetcher"]
