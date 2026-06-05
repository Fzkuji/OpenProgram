"""Feed provider call outcomes back to the credential pool so rotation /
cooldown / fallback actually engage.

The pool machinery (``auth/pool.py``) cools a credential down on failure and
skips it on the next acquire — but ONLY if someone reports the failure. These
helpers are that someone: the provider call path acquires a credential via
:func:`acquire_pooled` (recording exactly which one it used), then reports the
result via :func:`report_success` / :func:`report_failure`. A 429 on key #0 cools
it down; the outer runtime retry re-acquires and the pool hands back key #1.

No-op unless the provider has an AuthStore pool: env-key / OAuth / claude-code
providers (which resolve their token elsewhere) get ``None`` from
:func:`acquire_pooled` and never report, so nothing changes for them.

Telemetry must never break a request — every reporting call swallows its own
errors.
"""
from __future__ import annotations

from typing import Optional, Tuple


def acquire_pooled(
    provider_id: str,
    profile_id: Optional[str] = None,
) -> Optional[Tuple[str, str, str]]:
    """Pick a credential from the provider's pool for THIS request.

    Returns ``(token, profile_id, credential_id)`` so the caller can report the
    outcome against the exact credential it used — or ``None`` when the provider
    has no AuthStore pool (the caller then falls back to its own key resolution,
    e.g. ``opts.api_key`` / an env var / a Meridian token).

    The profile is the provider's active one (``auth/active.py``) unless given.
    Selection honours the pool strategy + cooldown skip (``auth/pool.py``), so a
    cooled-down credential is invisible here and the next healthy one is used.
    """
    from .store import get_store
    from .active import get_active_profile
    from .manager import get_manager
    from .types import AuthError, AuthConfigError

    prof = profile_id or get_active_profile(provider_id)
    pool = get_store().find_pool(provider_id, prof)
    if pool is None or not pool.credentials:
        return None
    try:
        cred = get_manager().acquire_sync(provider_id, prof)
    except (AuthError, AuthConfigError):
        return None
    from .resolver import _extract_token
    token = _extract_token(cred)
    if not token:
        return None
    return (token, cred.profile_id, cred.credential_id)


def classify_failure(status: Optional[int], error_text: str = "") -> str:
    """Map an HTTP status (or, lacking one, the error text) to a
    :func:`pool.mark_failure` reason. Conservative: an unknown error becomes a
    short ``server_error`` cooldown, never a permanent disable."""
    if status == 429:
        return "rate_limit"
    if status == 402:
        return "billing_blocked"
    if status in (401, 403):
        return "needs_reauth"
    if status is not None and 500 <= status < 600:
        return "server_error"
    t = (error_text or "").lower()
    if "rate limit" in t or "429" in t or "too many requests" in t:
        return "rate_limit"
    if "402" in t or "billing" in t or "insufficient" in t or "quota" in t:
        return "billing_blocked"
    if "401" in t or "403" in t or "unauthor" in t or "invalid api key" in t or "invalid_api_key" in t:
        return "needs_reauth"
    if "timeout" in t or "timed out" in t or "connection" in t or "network" in t:
        return "network_error"
    return "server_error"


def report_failure(
    provider_id: str,
    profile_id: str,
    credential_id: str,
    status: Optional[int] = None,
    error_text: str = "",
) -> None:
    """Cool the used credential down per the classified reason. Safe no-op if
    there's no credential id (the provider wasn't pool-backed)."""
    if not credential_id:
        return
    try:
        from .manager import get_manager
        get_manager().report_failure(
            provider_id, profile_id, credential_id,
            classify_failure(status, error_text),
            detail=(error_text or "")[:200],
        )
    except Exception:
        pass


def report_success(provider_id: str, profile_id: str, credential_id: str) -> None:
    """Clear transient error state on a 2xx. Cheap (no fsync); safe no-op when
    there's no credential id."""
    if not credential_id:
        return
    try:
        from .manager import get_manager
        get_manager().report_success(provider_id, profile_id, credential_id)
    except Exception:
        pass


__all__ = ["acquire_pooled", "classify_failure", "report_failure", "report_success"]
