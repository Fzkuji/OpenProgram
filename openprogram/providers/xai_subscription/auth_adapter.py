"""Grok subscription OAuth glue.

Uses the official public Grok CLI client (same id the Grok Build CLI
stores under ``auth.x.ai::<client>``). Browser PKCE against
``https://auth.x.ai``; the access token is a bearer for
``https://api.x.ai/v1`` chat completions.

xAI quirks (verified against the official CLI):

  * authorize must send ``plan=generic`` or loopback clients are rejected
  * token exchange must echo ``code_challenge`` + ``code_challenge_method``
  * redirect is ``http://127.0.0.1:56121/callback``
"""
from __future__ import annotations

import time
from typing import Optional

from openprogram.auth.credential_provider import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.types import Credential, CredentialData


PROVIDER_ID = "xai-subscription"

OAUTH_AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
OAUTH_TOKEN_URL = "https://auth.x.ai/oauth2/token"
OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OAUTH_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "grok-cli:access",
    "api:access",
]


def build_pkce_config():
    from openprogram.auth.methods.pkce_oauth import PkceConfig

    return PkceConfig(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
        client_id=OAUTH_CLIENT_ID,
        scopes=list(OAUTH_SCOPES),
        callback_port=56121,
        callback_path="/callback",
        callback_host="127.0.0.1",
        callback_bind_ip="127.0.0.1",
        extra_authorize_params={
            "plan": "generic",
            "referrer": "openprogram",
        },
        include_nonce=True,
        token_echo_challenge=True,
        also_prompt_paste=True,
    )


def _xai_refresh(cred: Credential) -> Credential:
    payload = cred.payload
    if payload.kind != "oauth":
        raise RuntimeError(
            f"xai-subscription refresh called with non-OAuth payload kind: {payload.kind!r}"
        )
    refresh_token = payload.data.get("refresh_token", "")
    if not refresh_token:
        raise RuntimeError("xai-subscription credential has no refresh_token")

    from openprogram.security.safe_http import safe_client

    with safe_client("provider.oauth.fixed") as client:
        response = client.post(
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
            timeout=30.0,
        )
    if response.status_code != 200:
        raise RuntimeError(f"OAuth refresh failed {response.status_code}")
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError("OAuth refresh response missing 'access_token'")

    expires_at_ms = int(time.time() * 1000) + int(data.get("expires_in") or 3600) * 1000
    new_refresh = data.get("refresh_token") or refresh_token
    new_payload = CredentialData(
        kind="oauth",
        auth_value=data["access_token"],
        data={
            "refresh_token": new_refresh,
            "expires_at_ms": expires_at_ms,
            "scope": payload.data.get("scope"),
            "client_id": payload.data.get("client_id") or OAUTH_CLIENT_ID,
            "token_endpoint": OAUTH_TOKEN_URL,
            "id_token": data.get("id_token", payload.data.get("id_token")),
            "extra": dict(payload.data.get("extra") or {}),
        },
    )
    return Credential(
        provider_id=cred.provider_id,
        account_id=cred.account_id,
        kind="oauth",
        payload=new_payload,
        status="valid",
        created_at_ms=cred.created_at_ms,
        updated_at_ms=int(time.time() * 1000),
        source=cred.source,
        metadata=dict(cred.metadata),
        cooldown_until_ms=0,
        last_used_at_ms=cred.last_used_at_ms,
        use_count=cred.use_count,
        last_error=None,
        read_only=False,
        credential_id=cred.credential_id,
    )


_REGISTERED = False


def register_xai_subscription_auth() -> None:
    global _REGISTERED
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=300,
            refresh=_xai_refresh,
            async_refresh=None,
        )
    )
    _REGISTERED = True


register_xai_subscription_auth()


def import_pasted_token(token: str, *, account_id: str = "default") -> Credential:
    """Store a pasted SuperGrok access token as an OAuth credential.

    Used when the user already has a bearer (browser page, ~/.grok/auth.json)
    and just wants to drop it on the Grok Subscription card.
    """
    token = (token or "").strip()
    if not token:
        raise RuntimeError("no access token pasted")
    now_ms = int(time.time() * 1000)
    return Credential(
        provider_id=PROVIDER_ID,
        account_id=account_id,
        kind="oauth",
        payload=CredentialData(
            kind="oauth",
            auth_value=token,
            data={
                "refresh_token": "",
                "expires_at_ms": now_ms + 7 * 24 * 3600 * 1000,
                "client_id": OAUTH_CLIENT_ID,
                "token_endpoint": OAUTH_TOKEN_URL,
            },
        ),
        status="valid",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        source="paste_token:xai-subscription",
        metadata={},
    )


__all__ = [
    "PROVIDER_ID",
    "build_pkce_config",
    "register_xai_subscription_auth",
    "import_pasted_token",
]
