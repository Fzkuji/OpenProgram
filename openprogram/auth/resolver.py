"""Single entry point callers use to resolve "the right credential, now".

The problem this solves: most call sites don't want to reason about
whether a provider uses OAuth, api_key, delegated-CLI, or env-var auth —
they just want a bearer string they can stick on the Authorization
header. :func:`resolve_api_key_sync` hides the ladder behind one call.

Resolution order:

  1. :func:`auth.context.get_credential_override` — lets tests or
     middleware inject a specific credential for this scope without
     writing the store.
  2. :meth:`CredentialProvider.acquire_sync` — the proper v2 path. Returns a
     refreshed access_token/api_key from the provider pool. If the
     provider isn't registered or the pool is empty, raises
     :class:`AuthConfigError`; we fall through rather than propagate.
  3. ``env_api_keys.resolve_provider_key`` — covers the Bedrock/Vertex
     cloud-credential sentinel (those have no bearer key). Env vars are
     NOT consulted anywhere; provider keys live in the AuthStore only.

Returns ``None`` if every step fails — caller decides whether that
triggers a "please log in" banner or just proceeds key-less (useful for
local model endpoints that don't need auth).

One deliberate exception to "fall through silently":
:class:`AuthCredentialProcessError` propagates. When the user configured a
helper command, the answer to "the helper is broken" is an error naming
the helper, not a quiet downgrade to a stale key from another layer.

Intentionally sync: most provider call sites are sync (FastAPI
dependencies, CLI entry points). Async callers should call
:meth:`CredentialProvider.acquire` directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .account_selection import get_active_account
from .context import (
    get_active_account_id,
    get_credential_override,
)
from .credential_provider import get_credential_provider
from .types import (
    AuthConfigError,
    AuthError,
    AuthCredentialProcessError,
    Credential,
)


@dataclass
class ResolvedConnection:
    """What one request needs, translated from a Credential.

    ``kind`` is the credential kind carried through so wire code can
    still branch on it if needed; ``auth_value`` is always the bearer
    string to use (never a raw payload attribute name); ``base_url``
    is ``None`` (not empty string) when unset, so wire code's
    catalog-default fallback triggers correctly.
    """

    kind: str
    auth_value: str
    base_url: Optional[str]
    headers: dict


def resolve_connection(cred: "Credential") -> "ResolvedConnection | None":
    """Translate a Credential into what one request needs.

    cli_delegated reads its external file here for the freshest token.
    credential_process runs its helper here (cached for ``cache_seconds``)
    and raises :class:`AuthCredentialProcessError` if the helper fails —
    a user who configured a helper must not have it silently replaced by
    whatever other credential the ladder finds. sso raises
    :class:`AuthConfigError`; the kind exists but no flow implements it.
    """
    p = cred.payload
    kind = getattr(p, "kind", "")
    auth_value = p.auth_value
    if kind == "cli_delegated":
        auth_value = _read_delegated_token(p) or ""
    elif kind == "credential_process":
        from .methods.credential_process import token_for_payload
        auth_value = token_for_payload(
            p.data, scope=f"{cred.provider_id}/{cred.account_id}",
        )
    elif kind == "sso":
        raise AuthConfigError(
            "enterprise SSO credentials are not implemented; "
            "no login flow can produce or use kind='sso'",
            provider_id=cred.provider_id, account_id=cred.account_id,
        )
    if not auth_value:
        return None
    base_url = p.base_url or None
    return ResolvedConnection(
        kind=kind, auth_value=auth_value,
        base_url=base_url, headers=dict(p.headers or {}),
    )


def resolve_api_key_sync(
    provider_id: str,
    account_id: Optional[str] = None,
) -> Optional[str]:
    """Return a bearer string for the provider, or None if no path yields one.

    ``account_id`` defaults to the provider's active account
    (:func:`auth.account_selection.get_active_account` — its pinned account, else the
    ambient scope, else ``"default"``). Explicit override is useful for scripts
    that want a specific account regardless of the active selection.
    """
    account = account_id or get_active_account(provider_id)

    # Layer 1 — scope-injected override (tests, DI).
    override = get_credential_override(provider_id)
    if override is not None:
        token = _extract_token(override)
        if token:
            return token

    # Layer 2 — CredentialProvider.
    try:
        cred = get_credential_provider().acquire_sync(provider_id, account)
        token = _extract_token(cred)
        if token:
            return token
    except AuthCredentialProcessError:
        # The user configured this helper. A broken helper is their bug
        # to fix, not a cue to hand back some other layer's credential.
        raise
    except (AuthConfigError, AuthError):
        # Fall through silently — these are expected when the provider
        # simply hasn't been registered in the new system yet.
        pass
    except RuntimeError:
        # Running inside an event loop — callers in that situation should
        # use the async API. Don't crash the whole resolver; fall through
        # to env-var path so legacy code keeps working.
        pass

    # Layer 3 — the Bedrock/Vertex cloud-credential sentinel (no bearer
    # key exists for those; "<authenticated>" means the chain is ready).
    try:
        from openprogram.providers.env_api_keys import resolve_provider_key
    except ImportError:
        return None
    return resolve_provider_key(provider_id) or None


def resolve_store_api_key_sync(
    provider_id: str,
    account_id: Optional[str] = None,
) -> Optional[str]:
    """API-key-shaped credential from the AuthStore only, or None.

    Unlike :func:`resolve_api_key_sync`, OAuth/device tokens are
    EXCLUDED: those belong to provider-specific transports (claude-code
    daemon, codex OAuth headers) — handing an access_token to a wire
    that puts it in ``x-api-key`` just 401s. This is the single key
    source ``resolve_provider_key`` reads.

    ``credential_process`` IS included: a helper prints a plain key that
    goes in the same header an ``api_key`` would. Helper failures raise
    rather than returning None, for the reason in the module docstring.
    """
    account = account_id or get_active_account(provider_id)

    override = get_credential_override(provider_id)
    if override is not None:
        payload = getattr(override, "payload", None)
        if payload is not None and getattr(payload, "kind", "") == "api_key":
            return payload.auth_value or None
        return None

    try:
        cred = get_credential_provider().acquire_sync(provider_id, account)
    except (AuthConfigError, AuthError, RuntimeError):
        return None
    payload = getattr(cred, "payload", None)
    if payload is not None and getattr(payload, "kind", "") == "credential_process":
        conn = resolve_connection(cred)
        return conn.auth_value if conn else None
    if payload is not None and getattr(payload, "kind", "") == "api_key":
        return payload.auth_value or None
    return None


def _extract_token(cred: Credential) -> Optional[str]:
    """Pull the bearer value out of whichever payload shape we got."""
    conn = resolve_connection(cred)
    return conn.auth_value if conn else None


def _read_delegated_token(payload) -> Optional[str]:
    """Read the access token out of a delegated CLI's on-disk store.

    ``payload.data["store_path"]`` points at the external CLI's auth file
    (e.g. codex's ``~/.codex/auth.json`` or Claude Code's
    ``~/.claude/.credentials.json``); ``payload.data["access_key_path"]``
    is the JSON key path to the access token inside it. Any read/parse
    failure yields None so the resolver falls through rather than raising —
    the caller then surfaces the actionable "no credential" error or
    re-login prompt.
    """
    import json
    from pathlib import Path

    store_path = payload.data.get("store_path")
    if not store_path:
        return None
    try:
        data = json.loads(Path(store_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    node: object = data
    for key in payload.data.get("access_key_path", []):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) and node else None


__all__ = [
    "resolve_api_key_sync",
    "resolve_store_api_key_sync",
    "resolve_connection",
    "ResolvedConnection",
]
