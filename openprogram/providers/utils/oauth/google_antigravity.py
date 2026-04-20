"""
Google Antigravity OAuth provider (Gemini 3, Claude, GPT-OSS via Google Cloud).

Implements local callback server + PKCE flow with project discovery/provisioning.

Mirrors utils/oauth/google-antigravity.ts
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode

import httpx

from openprogram.providers.utils.oauth.pkce import generate_pkce
from openprogram.providers.utils.oauth.types import OAuthAuthInfo, OAuthCredentials, OAuthLoginCallbacks, OAuthPrompt

import os as _os

_CLIENT_ID = _os.environ.get(
    "GOOGLE_ANTIGRAVITY_CLIENT_ID",
    "204917773984-g9e60fh5f55u8p3kiqf9pv8fkg5m0cgl.apps.googleusercontent.com",
)
_CLIENT_SECRET = _os.environ.get("GOOGLE_ANTIGRAVITY_CLIENT_SECRET", "")
_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REDIRECT_PORT = 9004
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}"
_SCOPES = "https://www.googleapis.com/auth/cloud-platform openid email"


async def login_antigravity(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Login with Antigravity OAuth (local callback server + PKCE)."""
    verifier, challenge = generate_pkce()

    auth_params = urlencode({
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{_AUTHORIZE_URL}?{auth_params}"
    callbacks.on_auth(OAuthAuthInfo(url=auth_url, instructions="Visit the URL and authorize access."))

    # Start local callback server to receive code
    code = await _wait_for_callback_code(callbacks)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "redirect_uri": _REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = int(time.time() * 1000) + data.get("expires_in", 3600) * 1000 - 5 * 60 * 1000
    return OAuthCredentials(
        refresh=data.get("refresh_token", ""),
        access=data.get("access_token", ""),
        expires=expires_at,
    )


async def refresh_antigravity_token(credentials: OAuthCredentials) -> OAuthCredentials:
    """Refresh a Google Cloud OAuth token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "refresh_token": credentials.refresh,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = int(time.time() * 1000) + data.get("expires_in", 3600) * 1000 - 5 * 60 * 1000
    return OAuthCredentials(
        refresh=credentials.refresh,
        access=data.get("access_token", credentials.access),
        expires=expires_at,
    )


async def _wait_for_callback_code(callbacks: OAuthLoginCallbacks) -> str:
    """Start a temporary HTTP server to receive the OAuth callback code."""
    code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    from aiohttp import web  # type: ignore[import]

    async def handle_callback(request: web.Request) -> web.Response:
        code = request.query.get("code", "")
        if code and not code_future.done():
            code_future.set_result(code)
        return web.Response(
            text="<html><body>Authorization successful! You can close this window.</body></html>",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/", handle_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", _REDIRECT_PORT)
    await site.start()

    try:
        code = await asyncio.wait_for(code_future, timeout=300)
    finally:
        await runner.cleanup()

    return code


class _AntigravityOAuthProvider:
    id = "google-antigravity"
    name = "Google Antigravity"
    uses_callback_server = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_antigravity(callbacks)

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        return await refresh_antigravity_token(credentials)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    def modify_models(self, models, credentials):
        return models


antigravity_oauth_provider = _AntigravityOAuthProvider()
