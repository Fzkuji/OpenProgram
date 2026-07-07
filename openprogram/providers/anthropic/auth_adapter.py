"""Anthropic auth adapter — registers with AuthManager, adopts Claude Code state.

Three credential routes the adapter exposes:

  1. **API key** — the legacy ``ANTHROPIC_API_KEY`` env var. Handled via
     :mod:`auth.sources.env` on the ``anthropic`` provider.

  2. **OAuth token** (``sk-ant-oat`` prefix) — minted by the
     ``claude login`` flow in Claude Code. We don't own the refresh path;
     Claude Code rotates it through its own Keychain/file, and we adopt
     the result read-only so the CLI remains authoritative. Import
     via :func:`import_from_claude_code`. If the on-disk token is past
     expiry, AuthManager surfaces :class:`AuthReadOnlyError` — the user
     must rerun ``claude login`` in their terminal (we tell them how).

  3. **OAuth via our own PKCE flow** — the Claude subscription browser
     login, same shape as openai-codex. ``build_pkce_config`` configures
     :class:`PkceLoginMethod` against ``claude.ai/oauth/authorize`` +
     ``console.anthropic.com/v1/oauth/token`` with the Claude Code OAuth
     client. The minted ``sk-ant-oat`` token carries a refresh_token, so
     we DO own rotation here — :func:`_anthropic_refresh` is registered.

  4. **setup-token paste** — headless fallback. The user mints a token
     out-of-band with ``claude setup-token`` and pastes it; we store it
     as an ``oauth`` credential WITHOUT a refresh_token (setup-tokens
     don't carry one, ~1y lifetime). Via :func:`import_setup_token`.

The provider config registers :func:`_anthropic_refresh`. It's a no-op
for credentials without a refresh_token (api_key / delegated / setup-token),
and rotates the PKCE-minted OAuth token when it nears expiry.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from openprogram.auth.manager import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.types import (
    Credential,
    CredentialData,
)


PROVIDER_ID = "anthropic"

# Claude subscription OAuth — the same client the official Claude Code login
# uses (publicly used by opencode / claude-code-login). Verified live: the
# authorize→token exchange returns an ``sk-ant-oat`` access_token WITH a
# refresh_token (8h access lifetime, rotated via refresh_token).
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
# Anthropic registers a FIXED hosted redirect that shows the user a
# ``code#state`` string to copy — there's no loopback callback.
OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
OAUTH_SCOPES = ["org:create_api_key", "user:profile", "user:inference"]


def build_pkce_config():
    """PKCE config for the Claude subscription browser login.

    Manual-paste flow: open ``claude.ai/oauth/authorize`` (with
    ``code=true`` so the page shows a copyable code), the user pastes the
    ``code#state`` back, we exchange it as JSON at the console token
    endpoint. Mirrors openai-codex's ``build_pkce_config`` shape.
    """
    from openprogram.auth.methods.pkce_oauth import PkceConfig
    return PkceConfig(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
        client_id=OAUTH_CLIENT_ID,
        scopes=OAUTH_SCOPES,
        manual_paste_only=True,
        redirect_uri_override=OAUTH_REDIRECT_URI,
        token_use_json=True,
        extra_authorize_params={"code": "true"},
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def claude_code_credentials_path() -> Path:
    """Return the filesystem path to Claude Code's credentials file.

    The canonical on-disk location is ``~/.claude/.credentials.json``.
    macOS users may alternatively have the payload in the Keychain under
    service ``Claude Code-credentials``; Keychain adoption is a follow-up
    (needs a ``security find-generic-password`` external-process hook).
    """
    return Path.home() / ".claude" / ".credentials.json"


# ---------------------------------------------------------------------------
# Import from Claude Code
# ---------------------------------------------------------------------------

def import_from_claude_code(
    *,
    profile_id: str = "default",
    path: Optional[Path] = None,
) -> Optional[Credential]:
    """Read Claude Code's credentials file and produce a delegated OAuth
    credential.

    Returns ``None`` if the file is missing or unusable — callers decide
    whether that's a "please log in" error or just "skip this route".

    The resulting credential is a ``cli_delegated`` :class:`CredentialData`,
    read-only. The Claude Code CLI owns rotation; every API call re-reads
    the file through AuthManager, so rotations propagate automatically.
    """
    target = Path(path) if path else claude_code_credentials_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oauth = data.get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        return None

    metadata: dict[str, Any] = {
        "imported_from": "claude_code",
        "source_path": str(target),
        "platform": sys.platform,
    }
    if oauth.get("subscriptionType"):
        metadata["subscription_type"] = oauth["subscriptionType"]
    if oauth.get("scopes"):
        metadata["scopes"] = oauth["scopes"]

    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="cli_delegated",
        payload=CredentialData(
            kind="cli_delegated",
            data={
                "store_path": str(target),
                "access_key_path": ["claudeAiOauth", "accessToken"],
                "refresh_key_path": ["claudeAiOauth", "refreshToken"],
                "expires_key_path": ["claudeAiOauth", "expiresAt"],
            },
        ),
        source="claude_code_import",
        metadata=metadata,
        read_only=True,
    )


def import_api_key(
    api_key: str,
    *,
    profile_id: str = "default",
    metadata: Optional[dict[str, Any]] = None,
) -> Credential:
    """Wrap a pasted ANTHROPIC_API_KEY as a :class:`Credential`.

    Doesn't register it with the store — callers do that themselves via
    :meth:`AuthStore.add_credential`. Exists so every path that produces
    an Anthropic credential funnels through the same type construction,
    with uniform metadata.
    """
    md = {"imported_from": "paste"} | (metadata or {})
    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=api_key.strip()),
        source="anthropic_paste",
        metadata=md,
        read_only=False,
    )


def import_setup_token(
    token: str,
    *,
    profile_id: str = "default",
    metadata: Optional[dict[str, Any]] = None,
) -> Credential:
    """Wrap a pasted ``claude setup-token`` (sk-ant-oat…) as an OAuth credential.

    Setup-tokens carry NO refresh_token (≈1-year lifetime, no rotation),
    so we store an ``oauth`` :class:`CredentialData` with an empty
    refresh_token and a far-out expiry — _anthropic_refresh no-ops on it
    (it guards on refresh_token).
    The anthropic wire still routes it as a Bearer/Claude-Code request via
    the sk-ant-oat prefix sniff. Caller persists via AuthStore.
    """
    tok = token.strip()
    md = {"imported_from": "setup_token"} | (metadata or {})
    # ~1y out so the manager doesn't flag it stale before the token really
    # dies; if it expires early the request just 401s and the user re-pastes.
    far_future_ms = int(time.time() * 1000) + 365 * 24 * 3600 * 1000
    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="oauth",
        payload=CredentialData(
            kind="oauth",
            auth_value=tok,
            data={
                "refresh_token": "",
                "expires_at_ms": far_future_ms,
                "scope": list(OAUTH_SCOPES),
                "client_id": OAUTH_CLIENT_ID,
                "token_endpoint": OAUTH_TOKEN_URL,
            },
        ),
        source="anthropic_setup_token",
        metadata=md,
        read_only=False,
    )


# ---------------------------------------------------------------------------
# Refresh (PKCE-minted OAuth tokens carry a refresh_token; rotate them)
# ---------------------------------------------------------------------------

def _anthropic_refresh(cred: Credential) -> Credential:
    """Synchronous refresh — called by AuthManager via executor.

    No-op for credentials without a refresh_token (api_key won't reach
    here; setup-token / delegated have none). For a PKCE-minted OAuth
    token, POST the refresh_token to the console token endpoint and
    return a fresh credential with the same credential_id.
    """
    payload = cred.payload
    refresh_token = payload.data.get("refresh_token", "") if payload.kind == "oauth" else ""
    if payload.kind != "oauth" or not refresh_token:
        # Nothing to rotate — hand the credential back unchanged.
        return cred

    import httpx
    resp = httpx.post(
        payload.data.get("token_endpoint") or OAUTH_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": payload.data.get("client_id") or OAUTH_CLIENT_ID,
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Anthropic OAuth refresh failed {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json()
    for k in ("access_token", "expires_in"):
        if k not in data:
            raise RuntimeError(f"Anthropic OAuth refresh response missing {k!r}")

    expires_at_ms = int(time.time() * 1000) + int(data["expires_in"]) * 1000
    new_payload = CredentialData(
        kind="oauth",
        auth_value=data["access_token"],
        data={
            # Anthropic rotates the refresh_token; keep the old one if the
            # response omits it (some token endpoints reuse it).
            "refresh_token": data.get("refresh_token") or refresh_token,
            "expires_at_ms": expires_at_ms,
            "scope": payload.data.get("scope"),
            "client_id": payload.data.get("client_id") or OAUTH_CLIENT_ID,
            "token_endpoint": payload.data.get("token_endpoint") or OAUTH_TOKEN_URL,
            "id_token": data.get("id_token", payload.data.get("id_token")),
            "extra": dict(payload.data.get("extra") or {}),
        },
    )
    return Credential(
        provider_id=cred.provider_id,
        profile_id=cred.profile_id,
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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_anthropic_auth() -> None:
    """Register the Anthropic provider config with :mod:`auth.manager`.

    Called at module import. Idempotent; tests that need to swap refresh
    or failure-policy can call :func:`register_provider_config` directly
    — last registration wins.
    """
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=60,
            # Rotates PKCE-minted OAuth tokens (refresh_token present);
            # no-ops for api_key / setup-token / delegated (no refresh_token).
            refresh=_anthropic_refresh,
            async_refresh=None,
        )
    )


register_anthropic_auth()


__all__ = [
    "PROVIDER_ID",
    "OAUTH_CLIENT_ID",
    "OAUTH_AUTHORIZE_URL",
    "OAUTH_TOKEN_URL",
    "OAUTH_REDIRECT_URI",
    "OAUTH_SCOPES",
    "build_pkce_config",
    "claude_code_credentials_path",
    "import_from_claude_code",
    "import_api_key",
    "import_setup_token",
    "_anthropic_refresh",
    "register_anthropic_auth",
]
