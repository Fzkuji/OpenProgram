"""
OpenAI Codex OAuth provider (ChatGPT Plus/Pro).

Implements PKCE + local callback server for ChatGPT OAuth.

Mirrors utils/oauth/openai-codex.ts
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from urllib.parse import urlencode

import httpx

from openprogram.providers.utils.oauth.pkce import generate_pkce
from openprogram.providers.utils.oauth.types import OAuthAuthInfo, OAuthCredentials, OAuthLoginCallbacks

_CLIENT_ID = "pdlLIX2Y72MIl2rhLhTE9VV9bN905kBh"
_AUTHORIZE_URL = "https://auth.openai.com/authorize"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REDIRECT_PORT = 9006
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}/callback"
_AUDIENCE = "https://api.openai.com/v1"
_SCOPES = "openid email profile offline_access model.request model.read organization.read organization.write"


def _get_account_id_from_jwt(access_token: str) -> str | None:
    """Extract account ID from a JWT token's claims."""
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        auth_info = payload.get("https://api.openai.com/auth", {})
        return auth_info.get("user_id") or auth_info.get("sub")
    except Exception:
        return None


async def login_openai_codex(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Login with OpenAI Codex OAuth (PKCE + local callback server)."""
    verifier, challenge = generate_pkce()

    auth_params = urlencode({
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPES,
        "audience": _AUDIENCE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{_AUTHORIZE_URL}?{auth_params}"
    callbacks.on_auth(OAuthAuthInfo(url=auth_url, instructions="Visit the URL to authorize ChatGPT access."))

    code = await _wait_for_callback_code()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = int(time.time() * 1000) + data.get("expires_in", 3600) * 1000 - 5 * 60 * 1000
    creds = OAuthCredentials(
        refresh=data.get("refresh_token", ""),
        access=data.get("access_token", ""),
        expires=expires_at,
    )
    account_id = _get_account_id_from_jwt(creds.access)
    if account_id:
        creds.extra["account_id"] = account_id
    return creds


async def refresh_openai_codex_token(credentials: OAuthCredentials) -> OAuthCredentials:
    """Refresh OpenAI Codex OAuth token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": _CLIENT_ID,
                "refresh_token": credentials.refresh,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = int(time.time() * 1000) + data.get("expires_in", 3600) * 1000 - 5 * 60 * 1000
    creds = OAuthCredentials(
        refresh=data.get("refresh_token", credentials.refresh),
        access=data.get("access_token", credentials.access),
        expires=expires_at,
        extra=dict(credentials.extra),
    )
    account_id = _get_account_id_from_jwt(creds.access)
    if account_id:
        creds.extra["account_id"] = account_id
    return creds


async def _wait_for_callback_code() -> str:
    """Start a local HTTP server to receive the OAuth callback."""
    code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    try:
        from aiohttp import web  # type: ignore[import]

        async def handle(request: web.Request) -> web.Response:
            code = request.query.get("code", "")
            if code and not code_future.done():
                code_future.set_result(code)
            return web.Response(
                text="<html><body>Authorization complete! You can close this window.</body></html>",
                content_type="text/html",
            )

        app = web.Application()
        app.router.add_get("/callback", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", _REDIRECT_PORT)
        await site.start()
        try:
            return await asyncio.wait_for(code_future, timeout=300)
        finally:
            await runner.cleanup()

    except ImportError:
        return await asyncio.get_event_loop().run_in_executor(
            None, input, "Enter the authorization code from the callback URL: "
        )


class _OpenAICodexOAuthProvider:
    id = "openai-codex"
    name = "OpenAI Codex (ChatGPT Plus/Pro)"
    uses_callback_server = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_openai_codex(callbacks)

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        return await refresh_openai_codex_token(credentials)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    def modify_models(self, models, credentials):
        account_id = credentials.extra.get("account_id", "")
        if account_id:
            for model in models:
                if hasattr(model, "base_url") and model.base_url:
                    pass  # Would update base URL with account ID
        return models


openai_codex_oauth_provider = _OpenAICodexOAuthProvider()
