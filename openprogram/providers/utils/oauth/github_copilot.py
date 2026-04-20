"""
GitHub Copilot OAuth provider.

Implements device code flow with enterprise domain support.

Mirrors utils/oauth/github-copilot.ts
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from openprogram.providers.utils.oauth.types import OAuthAuthInfo, OAuthCredentials, OAuthLoginCallbacks, OAuthPrompt

_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_DEFAULT_BASE_URL = "https://api.github.com"
_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


def normalize_domain(domain: str) -> str:
    """Normalize a domain string, stripping protocol if present."""
    domain = domain.strip()
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    return parsed.netloc or parsed.path


def get_github_copilot_base_url(domain: str | None = None) -> str:
    """Get the GitHub Copilot API base URL for a domain."""
    if not domain:
        return _DEFAULT_BASE_URL
    norm = normalize_domain(domain)
    return f"https://{norm}/api/v3"


async def login_github_copilot(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Login with GitHub Copilot device code flow."""
    async with httpx.AsyncClient() as client:
        # Step 1: Request device code
        resp = await client.post(
            _DEVICE_CODE_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": _CLIENT_ID,
                "scope": "read:user",
            },
        )
        resp.raise_for_status()
        device_data = resp.json()

        verification_url = device_data.get("verification_uri", "https://github.com/login/device")
        user_code = device_data.get("user_code", "")
        device_code = device_data.get("device_code", "")
        interval = device_data.get("interval", 5)

        # Notify user
        callbacks.on_auth(OAuthAuthInfo(
            url=verification_url,
            instructions=f"Enter code: {user_code}",
        ))
        if callbacks.on_progress:
            callbacks.on_progress(f"Visit {verification_url} and enter code: {user_code}")

        # Step 2: Poll for access token
        while True:
            await asyncio.sleep(interval)
            poll_resp = await client.post(
                _ACCESS_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": _CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            poll_data = poll_resp.json()

            if "access_token" in poll_data:
                access_token = poll_data["access_token"]
                expires_at = int(time.time() * 1000) + 8 * 3600 * 1000  # 8 hours
                return OAuthCredentials(
                    refresh="",
                    access=access_token,
                    expires=expires_at,
                )

            error = poll_data.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error in ("access_denied", "expired_token"):
                raise RuntimeError(f"GitHub login failed: {error}")


async def refresh_github_copilot_token(credentials: OAuthCredentials) -> OAuthCredentials:
    """GitHub tokens don't expire quickly; return as-is (no refresh endpoint)."""
    return OAuthCredentials(
        refresh=credentials.refresh,
        access=credentials.access,
        expires=int(time.time() * 1000) + 8 * 3600 * 1000,
    )


class _GitHubCopilotOAuthProvider:
    id = "github-copilot"
    name = "GitHub Copilot"
    uses_callback_server = False

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_github_copilot(callbacks)

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        return await refresh_github_copilot_token(credentials)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    def modify_models(self, models, credentials):
        return models


github_copilot_oauth_provider = _GitHubCopilotOAuthProvider()
