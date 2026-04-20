"""
OAuth credential management for AI providers.

Central registry and utilities for OAuth-based authentication.

Mirrors utils/oauth/index.ts
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from openprogram.providers.utils.oauth.anthropic import anthropic_oauth_provider, login_anthropic, refresh_anthropic_token
from openprogram.providers.utils.oauth.github_copilot import (
    get_github_copilot_base_url,
    github_copilot_oauth_provider,
    login_github_copilot,
    normalize_domain,
    refresh_github_copilot_token,
)
from openprogram.providers.utils.oauth.google_antigravity import (
    antigravity_oauth_provider,
    login_antigravity,
    refresh_antigravity_token,
)
from openprogram.providers.utils.oauth.google_gemini_cli import (
    gemini_cli_oauth_provider,
    login_gemini_cli,
    refresh_google_cloud_token,
)
from openprogram.providers.utils.oauth.openai_codex import (
    login_openai_codex,
    openai_codex_oauth_provider,
    refresh_openai_codex_token,
)
from openprogram.providers.utils.oauth.types import (
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
    "anthropic_oauth_provider",
    "github_copilot_oauth_provider",
    "gemini_cli_oauth_provider",
    "antigravity_oauth_provider",
    "openai_codex_oauth_provider",
]

# ---------------------------------------------------------------------------
# Provider Registry
# ---------------------------------------------------------------------------

_oauth_provider_registry: dict[str, OAuthProviderInterface] = {
    anthropic_oauth_provider.id: anthropic_oauth_provider,
    github_copilot_oauth_provider.id: github_copilot_oauth_provider,
    gemini_cli_oauth_provider.id: gemini_cli_oauth_provider,
    antigravity_oauth_provider.id: antigravity_oauth_provider,
    openai_codex_oauth_provider.id: openai_codex_oauth_provider,
}


def get_oauth_provider(id_: OAuthProviderId) -> OAuthProviderInterface | None:
    """Get an OAuth provider by ID."""
    return _oauth_provider_registry.get(id_)


def register_oauth_provider(provider: OAuthProviderInterface) -> None:
    """Register a custom OAuth provider."""
    _oauth_provider_registry[provider.id] = provider


def get_oauth_providers() -> list[OAuthProviderInterface]:
    """Get all registered OAuth providers."""
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
