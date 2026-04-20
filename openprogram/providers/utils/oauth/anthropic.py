"""
Anthropic OAuth provider (Claude Pro/Max).

Implements PKCE-based OAuth authorization code flow.

Mirrors utils/oauth/anthropic.ts
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx

from openprogram.providers.utils.oauth.pkce import generate_pkce
from openprogram.providers.utils.oauth.types import OAuthAuthInfo, OAuthCredentials, OAuthLoginCallbacks, OAuthPrompt

# Obfuscated to avoid scrapers
_CLIENT_ID = base64.b64decode("OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl").decode()
_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
_SCOPES = "org:create_api_key user:profile user:inference"


async def login_anthropic(
    on_auth_url: "callable",
    on_prompt_code: "callable",
) -> OAuthCredentials:
    """Login with Anthropic OAuth (PKCE authorization code flow)."""
    verifier, challenge = generate_pkce()

    auth_params = urlencode({
        "code": "true",
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    })
    auth_url = f"{_AUTHORIZE_URL}?{auth_params}"
    on_auth_url(auth_url)

    auth_code: str = await on_prompt_code()
    splits = auth_code.split("#", 1)
    code = splits[0]
    state = splits[1] if len(splits) > 1 else ""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": code,
                "state": state,
                "redirect_uri": _REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        if not resp.is_success:
            raise RuntimeError(f"Token exchange failed: {resp.text}")

        data = resp.json()

    expires_at = int(time.time() * 1000) + data["expires_in"] * 1000 - 5 * 60 * 1000
    return OAuthCredentials(
        refresh=data["refresh_token"],
        access=data["access_token"],
        expires=expires_at,
    )


async def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """Refresh an expired Anthropic OAuth token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": _CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        if not resp.is_success:
            raise RuntimeError(f"Anthropic token refresh failed: {resp.text}")
        data = resp.json()

    expires_at = int(time.time() * 1000) + data["expires_in"] * 1000 - 5 * 60 * 1000
    return OAuthCredentials(
        refresh=data["refresh_token"],
        access=data["access_token"],
        expires=expires_at,
    )


class _AnthropicOAuthProvider:
    id = "anthropic"
    name = "Anthropic (Claude Pro/Max)"
    uses_callback_server = False

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_anthropic(
            on_auth_url=lambda url: callbacks.on_auth(OAuthAuthInfo(url=url)),
            on_prompt_code=lambda: callbacks.on_prompt(OAuthPrompt(message="Paste the authorization code:")),
        )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        return await refresh_anthropic_token(credentials.refresh)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    def modify_models(self, models, credentials):
        return models


anthropic_oauth_provider = _AnthropicOAuthProvider()
