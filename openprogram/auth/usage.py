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

from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .resolver import ResolvedConnection


def acquire_pooled(
    provider_id: str,
    profile_id: Optional[str] = None,
) -> Optional[Tuple["ResolvedConnection", str, str]]:
    """Pick a credential from the provider's pool for THIS request.

    Returns ``(conn, profile_id, credential_id)`` — ``conn`` is a
    :class:`~openprogram.auth.resolver.ResolvedConnection` carrying the bearer
    value plus any credential-specific ``base_url``/``headers`` — so the
    caller can report the outcome against the exact credential it used, and
    can let the credential's own connection info override the catalog
    default. Returns ``None`` when the provider has no AuthStore pool (the
    caller then falls back to its own key resolution, e.g. ``opts.api_key`` /
    an env var / a Meridian token).

    Normally the provider's ACTIVE account (profile, ``auth/active.py``) is used.
    When rotation is ON for the provider (``auth/rotation.py``), a request instead
    rotates across ALL the provider's accounts (profiles) by the chosen strategy,
    skipping ones whose credential is cooling down — so a 429 on one account fails
    over to the next. ``profile_id`` (explicit) always pins one account, bypassing
    rotation.
    """
    from .store import get_store
    from .active import get_active_profile
    from .manager import get_manager
    from .types import AuthError, AuthConfigError
    from .resolver import resolve_connection

    store = get_store()
    mgr = get_manager()

    def _resolve(prof: str) -> Optional[Tuple["ResolvedConnection", str, str]]:
        pool = store.find_pool(provider_id, prof)
        if pool is None or not pool.credentials:
            return None
        try:
            cred = mgr.acquire_sync(provider_id, prof)
        except (AuthError, AuthConfigError):
            return None
        conn = resolve_connection(cred)
        return (conn, cred.profile_id, cred.credential_id) if conn else None

    # Explicit profile pins one account (no rotation).
    if profile_id is not None:
        return _resolve(profile_id)

    from .rotation import get_rotation
    rot = get_rotation(provider_id)
    if rot["enabled"]:
        pools = [p for p in store.list_pools()
                 if p.provider_id == provider_id and p.credentials]
        # Drop accounts the user turned OFF for rotation. If that leaves
        # nothing (every account disabled), ignore the exclusions rather than
        # break the request.
        from .enabled import get_disabled
        disabled = get_disabled(provider_id)
        kept = [p for p in pools if p.profile_id not in disabled]
        pools = kept or pools
        chosen = _pick_account(provider_id, pools, rot["strategy"])
        if chosen is not None:
            got = _resolve(chosen.profile_id)
            if got is not None:
                return got
        # fall through to the active account if rotation found nothing usable

    return _resolve(get_active_profile(provider_id))


# Round-robin cursor per provider for account rotation (process-lifetime; a
# manual reorder/restart resets it — fine for spreading load across accounts).
_RR_ACCOUNT_CURSOR: dict = {}


def _account_healthy(pool, now_ms: int) -> bool:
    """An account is healthy if its (primary) credential isn't stopped or throttled."""
    c = pool.credentials[0] if pool.credentials else None
    if c is None:
        return False
    if getattr(c, "status", "") in ("revoked", "needs_reauth", "billing_blocked"):
        return False
    until = getattr(c, "cooldown_until_ms", 0) or 0
    return not (until and until > now_ms)


def _pick_account(provider_id: str, pools: list, strategy: str):
    """Pick one account (pool) to use this request, by ``strategy``, preferring
    healthy ones. ``pools`` is non-empty for the rotating path."""
    if not pools:
        return None
    import time as _t
    now = int(_t.time() * 1000)
    from .order import sort_key
    _k = sort_key(provider_id)             # the user's drag order is the priority
    pools = sorted(pools, key=lambda p: _k(p.profile_id))
    healthy = [p for p in pools if _account_healthy(p, now)]
    candidates = healthy or pools  # all cooling → use one anyway (better than nothing)
    if strategy == "round_robin":
        i = _RR_ACCOUNT_CURSOR.get(provider_id, 0) % len(candidates)
        _RR_ACCOUNT_CURSOR[provider_id] = i + 1
        return candidates[i]
    if strategy == "random":
        import random
        return random.choice(candidates)
    if strategy == "least_used":
        return min(candidates, key=lambda p: getattr(p.credentials[0], "use_count", 0) or 0)
    return candidates[0]  # fill_first


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
    if status is not None and 400 <= status < 500:
        # Remaining 4xx (404 model-not-found, 400 bad request, 422 …) are
        # request/model-level failures — the credential is fine, and cooling
        # it would punish every other model on this key for one bad request.
        return "request_error"
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
    """Record a KEY-level failure on the used credential. Safe no-op if
    there's no credential id (the provider wasn't pool-backed).

    Only failures that say something about the credential itself reach
    the pool: 429 (throttled), 402 (out of credits), 401/403 (rejected).
    Request-level 4xx, upstream 5xx, and network errors say nothing
    about key health — they return without touching it, and the user
    sees the provider's own message in the chat error bubble."""
    if not credential_id:
        return
    reason = classify_failure(status, error_text)
    if reason in ("request_error", "server_error", "network_error"):
        return
    try:
        from .manager import get_manager
        get_manager().report_failure(
            provider_id, profile_id, credential_id,
            reason,
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
