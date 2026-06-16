"""Model-list fetchers — one module per provider, plus the dispatcher.

Each per-provider module exposes a single function ``_fetch_<provider>``
that takes ``(provider_id, timeout)`` and returns either:

* a list of model dicts (success), or
* a dict with an ``"error"`` key (failure with a human-readable
  message)

This file holds the ``_FETCHERS`` map (provider id → fetcher
function) and the ``fetch_models_remote`` orchestrator that:

1. Picks the right fetcher (explicit ``_FETCHERS`` entry first; falls
   back to ``_fetch_openai_compat`` for any provider in
   ``providers._FETCH_MODELS_PROVIDERS``).
2. Normalises every per-fetcher response into the entry shape
   ``replace_fetched_models`` wants — pulling out
   ``context_window`` / ``max_tokens`` from the several spellings
   different upstreams use, lifting cost hints, deriving thinking
   capability via ``thinking_catalog.derive_thinking_fields``.
3. Calls ``storage.replace_fetched_models`` to rotate the
   ``_source: "fetched"`` rows (preserving manual additions) and
   prune dead ids from ``enabled_models``.
"""
from __future__ import annotations

from typing import Any

# Per-provider fetchers
from .openai_compat import _fetch_openai_compat
from .anthropic import _fetch_anthropic
from .bedrock import _fetch_bedrock
from .claude_code import _fetch_claude_code
from .codex import _fetch_codex_live
from .deepseek import _fetch_deepseek
from .github_copilot import _fetch_github_copilot
from .google import _fetch_google


# Provider id → fetcher function. Providers in
# ``providers._FETCH_MODELS_PROVIDERS`` use ``_fetch_openai_compat`` by
# default; explicit entries here override that default.
_FETCHERS: dict[str, Any] = {
    "anthropic": _fetch_anthropic,
    # claude-code runs DIRECT on the anthropic subscription — fetch the live
    # model list from Anthropic's own /v1/models (Bearer OAuth), same as the
    # anthropic provider. (Was a Meridian-daemon proxy probe.)
    "claude-code": _fetch_anthropic,
    "openai-codex": _fetch_codex_live,
    "google": _fetch_google,
    "amazon-bedrock": _fetch_bedrock,
    "github-copilot": _fetch_github_copilot,
    "deepseek": _fetch_deepseek,  # /v1/models is id-only, enrich locally
}


def fetch_models_remote(provider_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Dispatch to a provider-specific fetcher, normalise the result,
    and rotate it into ``custom_models`` via
    ``storage.replace_fetched_models``.

    Returns ``{"fetched": N, "added": N, "removed": N, ...}`` on
    success, ``{"error": "..."}`` on failure.
    """
    from openprogram.providers.thinking_catalog import derive_thinking_fields

    from ..providers import _FETCH_MODELS_PROVIDERS, _default_api_for, _label
    from ..sources import enrich as _enrich_from_community
    from ..storage import replace_fetched_models

    fetcher = _FETCHERS.get(provider_id)
    # Providers that speak the Anthropic Messages wire format (minimax,
    # minimax-cn, …) expose Anthropic's GET /v1/models with x-api-key —
    # the OpenAI-compatible GET /models 404s on their /anthropic host.
    # Route them to the (now base_url-aware) Anthropic fetcher before the
    # OpenAI-compat fallback.
    if fetcher is None and _default_api_for(provider_id) == "anthropic-messages":
        fetcher = _fetch_anthropic
    if fetcher is None and provider_id in _FETCH_MODELS_PROVIDERS:
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
        reasoning_hint = bool(entry.get("reasoning") or reasoning_hint)
        # Derive thinking capability so newly-discovered models come
        # through with a working picker. Static data only — still
        # re-derived at read time in ``listing.list_models_for_provider``
        # to pick up override-table edits.
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

    # Fetch is authoritative: replace the previous fetched set rather
    # than merge into it. ``replace_fetched_models`` preserves any
    # rows the user added by hand (no ``_source: "fetched"`` marker)
    # so a power user can still pin a row that upstream doesn't list.
    result = replace_fetched_models(provider_id, models)
    return {
        "provider": provider_id,
        "fetched": len(models),
        "added": result["added"],
        "removed": result["removed"],
        "total_custom": result["total"],
        "dropped_enabled": result.get("dropped_enabled", []),
    }


__all__ = ["fetch_models_remote", "_FETCHERS"]
