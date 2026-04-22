"""Codex-specific glue for auth v2.

One file connecting three moving parts:

  * :class:`openprogram.auth.manager.AuthManager` — generic orchestrator
    that wants a ``refresh_fn`` per provider
  * :class:`openprogram.auth.sources.CodexCliSource` — discovers the
    existing ``~/.codex/auth.json`` state
  * this module's ``_codex_refresh`` — does the real OAuth call against
    ``auth.openai.com/oauth/token`` and writes the rotated tokens back
    to the Codex CLI's own file, so ``codex login`` / ``codex exec``
    keep working under the user's hand

Why write back to Codex's file at all: a Codex user may run both the
Codex CLI and OpenProgram against the same account. Rotating tokens
without updating ``auth.json`` would break the CLI. We mirror what the
CLI itself does on refresh — same JSON shape, same atomic tmp+rename.

On import, :func:`register_codex_auth` registers the provider config.
Import is idempotent so repeated registration from different modules is
safe; last registration wins (useful for tests that want to swap the
refresh fn).
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from openprogram.auth.manager import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.types import (
    Credential,
    OAuthPayload,
)


PROVIDER_ID = "openai-codex"

OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
JWT_CLAIM_PATH = "https://api.openai.com/auth"


def build_pkce_config():
    """PKCE OAuth config that matches the Codex CLI's own browser flow.

    Mirrors the authorize URL you get from ``codex login`` (which
    OpenClaw and the Codex CLI itself use): callback on
    ``localhost:1455/auth/callback``, ``codex_cli_simplified_flow=true``,
    ``id_token_add_organizations=true`` so the returned JWT carries
    the chatgpt_account_id our runtime needs.
    """
    from openprogram.auth.methods.pkce_oauth import PkceConfig
    return PkceConfig(
        authorize_url=OAUTH_AUTHORIZE_URL,
        token_url=OAUTH_TOKEN_URL,
        client_id=OAUTH_CLIENT_ID,
        scopes=["openid", "profile", "email", "offline_access"],
        callback_port=1455,
        callback_path="/auth/callback",
        extra_authorize_params={
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "openprogram",
        },
    )


# ---------------------------------------------------------------------------
# Path resolution (honors $CODEX_HOME exactly like Codex CLI)
# ---------------------------------------------------------------------------

def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if not configured:
        return Path.home() / ".codex"
    if configured in ("~", "~/"):
        return Path.home()
    if configured.startswith("~/"):
        return Path.home() / configured[2:]
    return Path(configured).resolve()


def codex_auth_path() -> Path:
    return _codex_home() / "auth.json"


# ---------------------------------------------------------------------------
# JWT helpers (ChatGPT's access token is a JWT carrying account_id)
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT: not 3 segments")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def extract_account_id(access_token: str) -> str:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get(JWT_CLAIM_PATH) or {}
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError(
            "JWT has no chatgpt_account_id — re-run `codex login --device-auth`"
        )
    return account_id.strip()


def jwt_expiry_epoch_ms(access_token: str) -> Optional[int]:
    try:
        exp = _decode_jwt_payload(access_token).get("exp")
        if isinstance(exp, (int, float)):
            return int(exp) * 1000
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Refresh — core of the adapter
# ---------------------------------------------------------------------------

def _codex_refresh(cred: Credential) -> Credential:
    """Synchronous refresh — called by AuthManager via executor.

    1. Use the current refresh_token to request new tokens from
       auth.openai.com.
    2. Rewrite ``~/.codex/auth.json`` so the Codex CLI shares our view.
    3. Return a fresh :class:`Credential` with the same ``credential_id``.
    """
    payload = cred.payload
    if not isinstance(payload, OAuthPayload):
        raise RuntimeError(
            f"codex refresh called with non-OAuth payload: {type(payload).__name__}"
        )
    if not payload.refresh_token:
        raise RuntimeError("codex credential has no refresh_token")

    response = httpx.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": payload.refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        },
        timeout=30.0,
    )
    if response.status_code != 200:
        # Rotation-consumed case: OpenAI returns 400 invalid_grant when
        # the refresh_token has already been used. AuthManager's
        # AuthRotationConsumedError path handles the reload+retry; we
        # surface it through the generic RuntimeError for now (manager
        # matches on RuntimeError in one codepath, on the typed error in
        # another).
        raise RuntimeError(
            f"OAuth refresh failed {response.status_code}: {response.text[:200]}"
        )
    data = response.json()
    for k in ("access_token", "refresh_token", "expires_in"):
        if k not in data:
            raise RuntimeError(f"OAuth refresh response missing {k!r}")

    expires_at_ms = int(time.time() * 1000) + int(data["expires_in"]) * 1000
    new_payload = OAuthPayload(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at_ms=expires_at_ms,
        scope=payload.scope,
        client_id=payload.client_id or OAUTH_CLIENT_ID,
        token_endpoint=OAUTH_TOKEN_URL,
        id_token=data.get("id_token", payload.id_token),
        extra=dict(payload.extra),
    )
    # Mirror back to ~/.codex/auth.json so Codex CLI sees rotated tokens.
    _write_back_to_codex_file(new_payload)

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


def _write_back_to_codex_file(payload: OAuthPayload) -> None:
    """Keep ``~/.codex/auth.json`` in sync after we rotate.

    Non-fatal on failure: if the file is gone or permission-denied, we
    still have the new tokens in our own store; the Codex CLI will just
    refresh independently next time the user runs it. We log nothing —
    the caller owns error reporting.
    """
    path = codex_auth_path()
    try:
        existing = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing.setdefault("auth_mode", "chatgpt")
    tokens = existing.setdefault("tokens", {})
    tokens["access_token"] = payload.access_token
    tokens["refresh_token"] = payload.refresh_token
    if payload.id_token:
        tokens["id_token"] = payload.id_token
    # Codex's own file doesn't store expires_at_ms — the CLI decodes the
    # JWT at read time. We leave it out for shape-compatibility.
    existing["last_refresh"] = _iso_now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Can't write? Silent; our own store is still authoritative.
        pass


def _iso_now() -> str:
    # Codex writes RFC3339-ish timestamps with ms precision. Match the shape.
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
# Importing from Codex CLI's file
# ---------------------------------------------------------------------------

def import_from_codex_file(
    *,
    profile_id: str = "default",
    auth_path: Optional[Path] = None,
) -> Optional[Credential]:
    """Read the Codex CLI's auth.json, produce an ``oauth`` credential.

    Returns ``None`` if the file is absent or unusable — callers decide
    whether that's a failure. Not to be confused with
    :class:`CodexCliSource` which produces a *delegated* (read-only)
    credential; this one produces a writable one so AuthManager can
    rotate it.
    """
    path = Path(auth_path) if auth_path else codex_auth_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Codex CLI stores two shapes:
    #   1. ChatGPT OAuth  → {"auth_mode": "chatgpt", "tokens": {...}}
    #   2. Bare API key   → {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-..."}
    #
    # Only the chatgpt shape drives the Responses backend this runtime
    # needs. The apikey shape has no chatgpt_account, no refresh path,
    # and semantically means "use OpenAI directly" — which belongs to
    # the `openai` provider, not here. We refuse to import it rather
    # than silently routing it to a different pool: the user asked to
    # import Codex credentials, and an apikey-mode file isn't one. If
    # they want OAuth, they need to re-run `codex login` without
    # --api-key. Matches OpenClaw's codex-cli-auth handling.
    auth_mode = (data.get("auth_mode") or "").lower()
    if auth_mode != "chatgpt":
        return None

    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access or not refresh:
        return None

    # Codex stores expiry inside the JWT, not as a separate field.
    expires_at_ms = jwt_expiry_epoch_ms(access) or 0
    metadata: dict[str, Any] = {"imported_from": "codex_cli", "source_path": str(path)}
    if tokens.get("account_id"):
        metadata["account_id"] = tokens["account_id"]
    else:
        try:
            metadata["account_id"] = extract_account_id(access)
        except RuntimeError:
            pass

    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="oauth",
        payload=OAuthPayload(
            access_token=access,
            refresh_token=refresh,
            expires_at_ms=expires_at_ms,
            client_id=OAUTH_CLIENT_ID,
            token_endpoint=OAUTH_TOKEN_URL,
            id_token=tokens.get("id_token", ""),
        ),
        source="codex_cli_import",
        metadata=metadata,
        read_only=False,
    )


# ---------------------------------------------------------------------------
# Registration (idempotent)
# ---------------------------------------------------------------------------

_REGISTERED = False


def register_codex_auth() -> None:
    """Register the Codex provider config with :mod:`auth.manager`.

    Called at module import. Idempotent. Tests that need to swap the
    refresh function can call :func:`openprogram.auth.manager.register_provider_config`
    directly — last registration wins.
    """
    global _REGISTERED
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=60,
            refresh=_codex_refresh,
            async_refresh=None,
        )
    )
    _REGISTERED = True


# Register on import — other modules don't have to remember to call us.
register_codex_auth()


__all__ = [
    "PROVIDER_ID",
    "codex_auth_path",
    "extract_account_id",
    "jwt_expiry_epoch_ms",
    "import_from_codex_file",
    "register_codex_auth",
]
