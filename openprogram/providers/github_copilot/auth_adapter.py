"""GitHub Copilot auth adapter.

Copilot's auth has two tiers:

  1. A **GitHub OAuth token** with ``read:user`` scope, minted by the
     device-code flow (see :mod:`providers.utils.oauth.github_copilot`).
     Long-lived in practice — GitHub OAuth access tokens don't expire on
     a short clock, so the existing refresh function is a no-op that
     extends the cached expiry window.

  2. A **Copilot API token** minted on demand by exchanging the GitHub
     OAuth token at ``api.github.com/copilot_internal/v2/token``. Used
     as the actual bearer for Copilot chat completions; expires in ~30
     minutes; we re-mint it from the GitHub OAuth token as needed.

The adapter registers Copilot with AuthManager using the **GitHub OAuth
token** as the primary credential. The ``api_token`` exchange happens
transparently inside :mod:`providers._shared.github_copilot_headers`
(unchanged). This split matches what OpenClaw / pi-ai do: the short-
lived api_token is never persisted in the credential store, only the
long-lived GitHub token is.

Env-var import routes: ``COPILOT_GITHUB_TOKEN`` → ``GH_TOKEN`` →
``GITHUB_TOKEN`` in priority order, matching :mod:`env_api_keys`.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from openprogram.auth.manager import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.types import (
    Credential,
    CredentialData,
)


PROVIDER_ID = "github-copilot"


# Ordered by preference — first non-empty wins.
_ENV_TOKEN_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def import_from_env_tokens(
    *,
    profile_id: str = "default",
) -> Optional[Credential]:
    """Read Copilot-compatible GitHub tokens from environment variables.

    Returns an ``api_key`` credential (read-only — env-owned) or ``None``
    if no matching variable is set. The resulting credential doesn't
    distinguish between a classic PAT and an OAuth access token; Copilot's
    internal token-exchange endpoint accepts either.
    """
    for var in _ENV_TOKEN_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return Credential(
                provider_id=PROVIDER_ID,
                profile_id=profile_id,
                kind="api_key",
                payload=CredentialData(kind="api_key", auth_value=value),
                source=f"env:{var}",
                metadata={"env_var": var},
                # Env-owned: we never rewrite the user's shell; refresh
                # is meaningless for a PAT. Mark read-only so rotation
                # attempts surface loudly instead of silently no-op'ing.
                read_only=True,
            )
    return None


def import_oauth_credential(
    access_token: str,
    refresh_token: str = "",
    *,
    profile_id: str = "default",
    expires_at_ms: int = 0,
    metadata: Optional[dict[str, Any]] = None,
) -> Credential:
    """Wrap the output of a device-code login as a :class:`Credential`.

    The caller ran :func:`providers.utils.oauth.github_copilot.login_github_copilot`
    and got an :class:`OAuthCredentials`; this helper translates it into
    the auth v2 shape without pulling :mod:`providers.utils.oauth` into
    the auth layer.
    """
    md = {"imported_from": "github_device_code"} | (metadata or {})
    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="oauth",
        payload=CredentialData(
            kind="oauth",
            auth_value=access_token,
            data={
                "refresh_token": refresh_token,
                "expires_at_ms": expires_at_ms,
                "client_id": "Iv1.b507a08c87ecfe98",
            },
        ),
        source="github_copilot_device_code",
        metadata=md,
        read_only=False,
    )


def register_github_copilot_auth() -> None:
    """Register Copilot with :mod:`auth.manager`.

    No refresh fn is registered: GitHub OAuth access tokens don't expire
    short-term, and the Copilot short-lived api_token is minted outside
    AuthManager's purview. If a future Copilot change shortens OAuth
    access-token lifetimes, add a ``refresh`` here pointing at
    :func:`providers.utils.oauth.github_copilot.refresh_github_copilot_token`
    (sync-wrapped).
    """
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=60,
            refresh=None,
            async_refresh=None,
        )
    )


register_github_copilot_auth()


__all__ = [
    "PROVIDER_ID",
    "import_from_env_tokens",
    "import_oauth_credential",
    "register_github_copilot_auth",
]
