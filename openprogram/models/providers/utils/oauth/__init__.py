"""
OAuth credential management for AI providers.

Central registry and utilities for OAuth-based authentication.

Mirrors utils/oauth/index.ts
"""

from __future__ import annotations

import time

from openprogram.models.providers.utils.oauth.github_copilot import github_copilot_oauth_provider
from openprogram.models.providers.utils.oauth.google_gemini_cli import gemini_cli_oauth_provider
from openprogram.models.providers.utils.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthProviderInterface,
    OAuthProviderId,
)

__all__ = [
    "OAuthCredentials",
    "OAuthProviderId",
    "OAuthPrompt",
    "OAuthAuthInfo",
    "OAuthLoginCallbacks",
    "OAuthProviderInterface",
    "get_oauth_provider",
    "register_oauth_provider",
    "get_oauth_providers",
    "get_oauth_api_key",
    "refresh_oauth_token",
    "github_copilot_oauth_provider",
    "gemini_cli_oauth_provider",
    "openai_codex_oauth_provider",
]

# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

_oauth_provider_registry: dict[str, OAuthProviderInterface] = {
    github_copilot_oauth_provider.id: github_copilot_oauth_provider,
    gemini_cli_oauth_provider.id: gemini_cli_oauth_provider,
}


def _ensure_openai_codex_registered() -> None:
    """Lazy: register openai_codex provider. Avoids circular import since
    openprogram.models.providers.adapters.openai_codex.oauth imports from utils.oauth.pkce."""
    from openprogram.models.providers.adapters.openai_codex.oauth import openai_codex_oauth_provider
    _oauth_provider_registry.setdefault(
        openai_codex_oauth_provider.id, openai_codex_oauth_provider
    )


def __getattr__(name: str):
    """PEP 562: lazy-expose openai_codex OAuth symbols without circular import."""
    if name in ("login_openai_codex", "openai_codex_oauth_provider", "refresh_openai_codex_token"):
        from openprogram.models.providers.adapters.openai_codex import oauth as _oc
        return getattr(_oc, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_oauth_provider(id_: OAuthProviderId) -> OAuthProviderInterface | None:
    """Get an OAuth provider by ID."""
    _ensure_openai_codex_registered()
    return _oauth_provider_registry.get(id_)


def register_oauth_provider(provider: OAuthProviderInterface) -> None:
    """Register a custom OAuth provider."""
    _oauth_provider_registry[provider.id] = provider


def get_oauth_providers() -> list[OAuthProviderInterface]:
    """Get all registered OAuth providers."""
    _ensure_openai_codex_registered()
    return list(_oauth_provider_registry.values())


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

async def refresh_oauth_token(
    provider_id: OAuthProviderId,
    credentials: OAuthCredentials,
) -> OAuthCredentials:
    """Refresh token for any OAuth provider."""
    provider = get_oauth_provider(provider_id)
    if not provider:
        raise ValueError(f"Unknown OAuth provider: {provider_id}")
    return await provider.refresh_token(credentials)


async def get_oauth_api_key(
    provider_id: OAuthProviderId,
    credentials_map: dict[str, dict],
) -> dict[str, OAuthCredentials | str] | None:
    """Get API key from stored OAuth credentials, refreshing if expired.

    Returns:
        Dict with 'new_credentials' and 'api_key', or None if no credentials.
    """
    provider = get_oauth_provider(provider_id)
    if not provider:
        raise ValueError(f"Unknown OAuth provider: {provider_id}")

    raw = credentials_map.get(provider_id)
    if not raw:
        return None

    creds = OAuthCredentials.from_dict(raw) if isinstance(raw, dict) else raw

    if time.time() * 1000 >= creds.expires:
        try:
            creds = await provider.refresh_token(creds)
        except Exception:
            raise RuntimeError(f"Failed to refresh OAuth token for {provider_id}")

    api_key = provider.get_api_key(creds)
    return {"new_credentials": creds, "api_key": api_key}
