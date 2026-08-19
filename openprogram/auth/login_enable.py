"""Subscription-login → config enablement.

Subscription providers (claude-code, openai-codex) have no list-models API,
so they used to inject "seed" model rows into the runtime registry at import
time — bypassing config entirely. Per docs/design/providers/models/models.md
§4.2 the correct behaviour is: on the user's behalf the program performs an
*enable*, writing spec rows to the same ``providers.<p>.models`` config list
the settings UI writes. This module is that enable.

``enable_default_models_on_login`` is the single seam, called after a
successful login from every surface (web login route, CLI ``providers login``)
and, as a first-run convenience, at provider import when credentials already
exist. It is idempotent and user-respecting: it writes the default set ONLY
when the provider currently has ZERO spec rows. A user who later disables one
of the defaults has a non-empty spec list, so a subsequent login/import never
resurrects it.
"""
from __future__ import annotations


# Default model sets written when a subscription provider is first enabled.
# (id, name, api, base_url, context_window, max_tokens, reasoning).
_DEFAULTS: dict[str, list[dict]] = {
    "claude-code": [
        {"id": "claude-opus-4-8", "name": "Claude Opus 4.8",
         "api": "anthropic-messages", "base_url": "https://api.anthropic.com",
         "input": ["text", "image"], "context_window": 1_000_000,
         "max_tokens": 128_000, "reasoning": True},
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6",
         "api": "anthropic-messages", "base_url": "https://api.anthropic.com",
         "input": ["text", "image"], "context_window": 1_000_000,
         "max_tokens": 128_000, "reasoning": True},
        {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5",
         "api": "anthropic-messages", "base_url": "https://api.anthropic.com",
         "input": ["text", "image"], "context_window": 200_000,
         "max_tokens": 64_000, "reasoning": False},
    ],
    # A SMALL sensible default, not the whole seed list of 11: the current
    # flagship chat model and the Codex coding variant this tool is built
    # around. The user fetches + enables the rest from Settings. thinking
    # fields are derived by the runtime's ensure_codex_model_registered when
    # it mirrors these into ENABLED_MODELS.
    "openai-codex": [
        {"id": "gpt-5.5", "name": "GPT-5.5", "api": "openai-codex",
         "reasoning": True},
        {"id": "gpt-5.5-codex", "name": "GPT-5.5 Codex", "api": "openai-codex",
         "reasoning": True},
    ],
}


def _has_credentials(provider_id: str) -> bool:
    """True if any credential pool holds a credential for this provider.

    Subscription credentials may live under a different pool id than the
    user-facing provider (claude-code shares the ``anthropic`` pool), so we
    resolve the pool id the same way the login flow does."""
    try:
        from openprogram.auth.login_driver import _credential_provider_id
        from openprogram.auth.store import get_store
        pool = _credential_provider_id(provider_id)
        return any(
            p.provider_id == pool and p.credentials
            for p in get_store().list_pools()
        )
    except Exception:
        return False


def enable_default_models_on_login(provider_id: str) -> list[str]:
    """Write the default model set for ``provider_id`` as config spec rows,
    marked ``source: "subscription-login"`` — but ONLY when the provider has
    zero spec rows. Returns the ids written (empty if nothing was written).

    Idempotence rule: defaults are written only on a *fresh* provider (no
    existing ``providers.<p>.models`` rows). Any prior enable/disable leaves a
    non-empty list, so a disabled default never resurrects.
    """
    defaults = _DEFAULTS.get(provider_id)
    if not defaults:
        return []
    # Lazy: auth must not hard-depend on the webui storage layer at import.
    from openprogram.webui._model_listing.storage import (
        _cache_lock,
        _read_providers_cfg,
        _upsert_spec_row,
        _write_providers_cfg,
    )
    written: list[str] = []
    with _cache_lock:
        cfg = _read_providers_cfg()
        pcfg = cfg.setdefault(provider_id, {})
        if pcfg.get("models"):
            return []  # not fresh — respect the user's enable/disable history
        for row in defaults:
            spec = dict(row, source="subscription-login")
            _upsert_spec_row(pcfg, spec)
            written.append(row["id"])
        pcfg.setdefault("enabled", True)
        _write_providers_cfg(cfg)
    if written:
        from openprogram.providers import enabled_models as mg
        mg.reload()
    return written


def seed_default_models_if_logged_in(provider_id: str) -> list[str]:
    """First-run convenience: if credentials for ``provider_id`` already exist
    (e.g. adopted from a vendor CLI) and the provider has no spec rows yet,
    enable the default set. No-op otherwise. Best-effort — never raises."""
    try:
        if _has_credentials(provider_id):
            return enable_default_models_on_login(provider_id)
    except Exception:
        pass
    return []
