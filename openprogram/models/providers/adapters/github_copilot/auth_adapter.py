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

The adapter registers Copilot with CredentialProvider using the **GitHub OAuth
token** as the primary credential. The ``api_token`` exchange happens
transparently inside :mod:`providers.shared.github_copilot_headers`
(unchanged). This split matches what OpenClaw / pi-ai do: the short-
lived api_token is never persisted in the credential store, only the
long-lived GitHub token is.

A GitHub token can also be pasted (:func:`import_pasted_token`) or read from
``COPILOT_GITHUB_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN``, which show as
read-only credential rows (``models/auth/store.py`` ``env_rows``).
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.models.auth.credential_provider import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.models.auth.types import (
    Credential,
    CredentialData,
)


PROVIDER_ID = "github-copilot"


def import_pasted_token(token: str) -> Credential:
    """A pasted GitHub token (a classic or fine-grained PAT, or an OAuth
    token) as a credential; Copilot's token exchange accepts either."""
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value:
        from openprogram.models.auth.types import AuthConfigError
        raise AuthConfigError("empty token — nothing to save", provider_id=PROVIDER_ID)
    return Credential(
        provider_id=PROVIDER_ID,
        kind="token",
        payload=CredentialData(kind="token", auth_value=value),
        source="web",
    )


def import_oauth_credential(
    access_token: str,
    refresh_token: str = "",
    *,
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
        source="web",
        metadata=md,
        read_only=False,
    )


def register_github_copilot_auth() -> None:
    """Register Copilot with :mod:`auth.credential_provider`.

    No refresh fn is registered: GitHub OAuth access tokens don't expire
    short-term, and the Copilot short-lived api_token is minted outside
    CredentialProvider's purview. If a future Copilot change shortens OAuth
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
    "import_pasted_token",
    "import_oauth_credential",
    "register_github_copilot_auth",
]
