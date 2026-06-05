"""Single entry point callers use to resolve "the right credential, now".

The problem this solves: most call sites don't want to reason about
whether a provider uses OAuth, api_key, delegated-CLI, or env-var auth —
they just want a bearer string they can stick on the Authorization
header. :func:`resolve_api_key_sync` hides the ladder behind one call.

Resolution order:

  1. :func:`auth.context.get_credential_override` — lets tests or
     middleware inject a specific credential for this scope without
     writing the store.
  2. :meth:`AuthManager.acquire_sync` — the proper v2 path. Returns a
     refreshed access_token/api_key from the provider pool. If the
     provider isn't registered or the pool is empty, raises
     :class:`AuthConfigError`; we fall through rather than propagate.
  3. ``env_api_keys.get_env_api_key`` — legacy path that reads
     ``OPENAI_API_KEY`` etc. Kept because users with unmigrated setups
     still expect it to work.

Returns ``None`` if every step fails — caller decides whether that
triggers a "please log in" banner or just proceeds key-less (useful for
local model endpoints that don't need auth).

Intentionally sync: most provider call sites are sync (FastAPI
dependencies, CLI entry points). Async callers should call
:meth:`AuthManager.acquire` directly.
"""
from __future__ import annotations

from typing import Optional

from .active import get_active_profile
from .context import (
    get_active_profile_id,
    get_credential_override,
)
from .manager import get_manager
from .types import (
    ApiKeyPayload,
    AuthConfigError,
    AuthError,
    Credential,
    OAuthPayload,
    DeviceCodePayload,
)


def resolve_api_key_sync(
    provider_id: str,
    profile_id: Optional[str] = None,
) -> Optional[str]:
    """Return a bearer string for the provider, or None if no path yields one.

    ``profile_id`` defaults to the provider's active profile
    (:func:`auth.active.get_active_profile` — its pinned account, else the
    ambient scope, else ``"default"``). Explicit override is useful for scripts
    that want a specific profile regardless of the active selection.
    """
    profile = profile_id or get_active_profile(provider_id)

    # Layer 1 — scope-injected override (tests, DI).
    override = get_credential_override(provider_id)
    if override is not None:
        token = _extract_token(override)
        if token:
            return token

    # Layer 2 — AuthManager.
    try:
        cred = get_manager().acquire_sync(provider_id, profile)
        token = _extract_token(cred)
        if token:
            return token
    except (AuthConfigError, AuthError):
        # Fall through silently — these are expected when the provider
        # simply hasn't been registered in the new system yet.
        pass
    except RuntimeError:
        # Running inside an event loop — callers in that situation should
        # use the async API. Don't crash the whole resolver; fall through
        # to env-var path so legacy code keeps working.
        pass

    # Layer 3 — legacy env vars.
    try:
        from openprogram.providers.env_api_keys import get_env_api_key
    except ImportError:
        return None
    legacy = get_env_api_key(provider_id)
    return legacy or None


def _extract_token(cred: Credential) -> Optional[str]:
    """Pull the bearer value out of whichever payload shape we got."""
    payload = cred.payload
    if isinstance(payload, ApiKeyPayload):
        return payload.api_key or None
    if isinstance(payload, (OAuthPayload, DeviceCodePayload)):
        return payload.access_token or None
    # Other payload kinds (cli_delegated, external_process, sso) need
    # specialized resolution that the caller-side code must perform (e.g.
    # re-read the delegated file). Returning None here tells the resolver
    # to fall through to the next layer rather than blindly returning a
    # stale token from metadata.
    return None


__all__ = ["resolve_api_key_sync"]
