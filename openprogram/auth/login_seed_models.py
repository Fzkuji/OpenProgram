"""Subscription-login → config enablement.

Providers without a usable account catalogue used to inject "seed" model
rows into the runtime registry at import time, bypassing config entirely. Per
docs/design/providers/models/overview.md
§4.2 the correct behaviour is: on the user's behalf the program performs an
*enable*, writing spec rows to the same ``providers.<p>.models`` config list
the settings UI writes. This module is that enable.

Codex and Grok now learn their model sets from the authenticated account API.
This module remains only for providers whose account API cannot provide that
catalogue. It is idempotent and user-respecting.
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
    # Lazy: keeps auth importable without touching config at import time.
    from openprogram.providers.storage import (
        _cache_lock,
        _update_providers_cfg,
        _upsert_spec_row,
    )
    written: list[str] = []
    with _cache_lock:

        def seed(cfg: dict) -> None:
            pcfg = cfg.setdefault(provider_id, {})
            if pcfg.get("models"):
                return
            for row in defaults:
                spec = dict(row, source="subscription-login")
                _upsert_spec_row(pcfg, spec)
                written.append(row["id"])
            pcfg.setdefault("enabled", True)

        _update_providers_cfg(seed)
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
