"""
Auth v2 — credential pool rotation + cooldown bookkeeping.

A :class:`CredentialPool` (in :mod:`.types`) holds several credentials
for one ``(provider, profile)``. This module decides which one the next
API call should use, and what happens to a credential that just returned
401 / 402 / 429 / 503.

Four strategies, all aware of cooldowns and disabled credentials:

  * ``fill_first``   — use index 0 while it's healthy; the rest only
    matter when the primary is cooling down. Matches the "backup key"
    mental model most users already have.
  * ``round_robin``  — cycle through healthy credentials. Good hedge
    against rate limits when all keys share the same quota bucket.
  * ``random``       — uniform pick among healthy credentials. Useful
    for fanout patterns where predictability leaks metadata.
  * ``least_used``   — pick the healthy credential with the lowest
    ``use_count``. Self-balancing across long-lived processes.

The pool never mutates a credential except to record usage / cooldown.
Writes that matter for persistence (refresh results, status changes) go
through :class:`AuthStore` so every mutation gets an event + file write
with the existing lock guarantees.

401/402/429 handling is a two-function split:

  * :func:`mark_failure` translates an HTTP-ish error into a cooldown +
    status update. The manager calls this when an API call raises.
  * :func:`pick` returns the next healthy credential, rotating if
    necessary; the manager calls this before dispatching an API call.

Keeping policy (strategy) separate from mechanism (cooldown bookkeeping)
means adding a fifth strategy later touches ~15 lines in one place.
"""
from __future__ import annotations

import random
import time
from typing import Optional

from .types import (
    AuthEvent,
    AuthEventType,
    AuthPoolExhaustedError,
    Credential,
    CredentialPool,
    CredentialStatus,
    PoolStrategy,
)

# Default cooldown durations per failure reason. Tuned to balance
# "don't hammer the API" against "don't lock the user out of a usable
# key for too long". Callers can override via the per-provider config
# (see :class:`PoolFailurePolicy`) so an enterprise deployment can be
# more aggressive.
DEFAULT_COOLDOWNS_MS: dict[str, int] = {
    "rate_limit": 60 * 1000,                   # 1 min; burst rate limits
    "rate_limit_long": 5 * 60 * 1000,          # 5 min; sustained rate limits
    "billing_blocked": 24 * 60 * 60 * 1000,    # 24 h; billing caps reset daily
    "server_error": 30 * 1000,                 # 30 s; upstream 5xx
    "network_error": 15 * 1000,                # 15 s; DNS / TCP hiccups
}


# ---------------------------------------------------------------------------
# Policy — per-provider knobs the manager passes in
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class PoolFailurePolicy:
    """Per-provider tuning for how harshly to penalize failures.

    Defaults are the usual OSS-agent values; enterprise deployments that
    see very different error budgets can override any key. Unknown
    providers fall back to defaults.
    """

    rate_limit_cooldown_ms: int = DEFAULT_COOLDOWNS_MS["rate_limit"]
    rate_limit_long_cooldown_ms: int = DEFAULT_COOLDOWNS_MS["rate_limit_long"]
    billing_cooldown_ms: int = DEFAULT_COOLDOWNS_MS["billing_blocked"]
    server_error_cooldown_ms: int = DEFAULT_COOLDOWNS_MS["server_error"]
    network_error_cooldown_ms: int = DEFAULT_COOLDOWNS_MS["network_error"]
    # How many consecutive errors before a credential is moved from
    # "cooldown" to "needs_reauth" — some backends return 401 for
    # expired tokens (transient) and also for revoked ones (permanent).
    # Without a counter we can't tell them apart; with one, we flip to
    # permanent after N refresh-and-retry cycles all fail.
    max_consecutive_errors: int = 5


# ---------------------------------------------------------------------------
# Picking — strategy-aware selection
# ---------------------------------------------------------------------------

def _is_healthy(cred: Credential, *, now_ms: int) -> bool:
    """A credential is healthy if no cooldown, not revoked, not needing reauth."""
    if cred.status in ("revoked", "needs_reauth"):
        return False
    if cred.cooldown_until_ms and cred.cooldown_until_ms > now_ms:
        return False
    return True


def _healthy_indices(pool: CredentialPool, now_ms: int) -> list[int]:
    return [i for i, c in enumerate(pool.credentials) if _is_healthy(c, now_ms=now_ms)]


def pick(pool: CredentialPool, *, now_ms: Optional[int] = None) -> Credential:
    """Return the next credential to use.

    Side effects: increments ``use_count`` and updates ``last_used_at_ms``
    on the picked credential, and (for round-robin) advances the pool's
    rotation cursor. None of these changes touch disk — that's the
    caller's job via :class:`AuthStore.put_pool`. Not auto-persisting
    here avoids a write-per-API-call; the manager writes only when
    cooldowns or status changes warrant it.

    Raises :class:`AuthPoolExhaustedError` if every credential is
    cooled-down or disabled.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    if not pool.credentials:
        raise AuthPoolExhaustedError(
            "pool has no credentials",
            provider_id=pool.provider_id, profile_id=pool.profile_id,
        )

    strategy = pool.strategy

    # "fixed" = rotation OFF: always the pinned credential (or the first), even
    # if it's cooling down — the user explicitly fixed this one. No failover; to
    # get failover they switch a rotating strategy on.
    if strategy == "fixed":
        idx = next(
            (i for i, c in enumerate(pool.credentials)
             if c.credential_id == pool.active_credential_id),
            0,
        )
        picked = pool.credentials[idx]
        picked.use_count += 1
        picked.last_used_at_ms = now
        return picked

    healthy = _healthy_indices(pool, now)
    if not healthy:
        raise AuthPoolExhaustedError(
            f"all {len(pool.credentials)} credentials in pool are unavailable",
            provider_id=pool.provider_id,
            profile_id=pool.profile_id,
        )

    if strategy == "fill_first":
        # The pinned credential is the default/first try; fall through the rest
        # by list order when it's cooled down.
        pinned = next(
            (i for i in healthy
             if pool.credentials[i].credential_id == pool.active_credential_id),
            None,
        )
        idx = pinned if pinned is not None else healthy[0]
    elif strategy == "round_robin":
        # Advance cursor past unhealthy entries; wrap around; if every
        # healthy slot was already visited in this cycle, just pick the
        # next healthy one after the cursor.
        cursor = pool._rr_cursor % max(len(pool.credentials), 1)
        # Scan forward for the first healthy slot at or after cursor.
        idx = next(
            (i for i in range(len(pool.credentials)) if (cursor + i) % len(pool.credentials) in healthy),
            healthy[0],
        )
        idx = (cursor + idx) % len(pool.credentials)
        pool._rr_cursor = (idx + 1) % len(pool.credentials)
    elif strategy == "random":
        idx = random.choice(healthy)
    elif strategy == "least_used":
        idx = min(healthy, key=lambda i: pool.credentials[i].use_count)
    else:
        # Defensive: caller loaded a pool with a strategy we don't
        # understand (forward-compat file). Fall back to fill_first.
        idx = healthy[0]

    picked = pool.credentials[idx]
    picked.use_count += 1
    picked.last_used_at_ms = now
    return picked


# ---------------------------------------------------------------------------
# Failure handling — mark + cooldown
# ---------------------------------------------------------------------------

def mark_failure(
    cred: Credential,
    reason: str,
    *,
    policy: Optional[PoolFailurePolicy] = None,
    detail: str = "",
    now_ms: Optional[int] = None,
) -> AuthEvent:
    """Mutate ``cred`` to record a failure and return the event to emit.

    ``reason`` is one of:

      * ``"rate_limit"`` — generic 429
      * ``"rate_limit_long"`` — 429 with Retry-After > short threshold
      * ``"billing_blocked"`` — 402 / explicit billing response
      * ``"server_error"`` — 5xx
      * ``"network_error"`` — DNS / TCP failure
      * ``"revoked"`` — explicit revocation; permanent
      * ``"needs_reauth"`` — refresh failed in a way that can't recover

    Not called for transient 401 that triggers a refresh — refresh has
    its own success/failure path in the manager, because refreshing
    doesn't make the key "unhealthy", it makes it "currently being
    worked on".
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    policy = policy or PoolFailurePolicy()
    cred.last_error = f"{reason}: {detail}" if detail else reason
    cred.updated_at_ms = now

    if reason == "revoked":
        cred.status = "revoked"
        cred.cooldown_until_ms = 0   # permanent; cooldown is meaningless
        return AuthEvent(
            type=AuthEventType.REVOKED,
            provider_id=cred.provider_id,
            profile_id=cred.profile_id,
            credential_id=cred.credential_id,
            detail={"reason": reason, "message": detail},
        )
    if reason == "needs_reauth":
        cred.status = "needs_reauth"
        cred.cooldown_until_ms = 0
        return AuthEvent(
            type=AuthEventType.NEEDS_REAUTH,
            provider_id=cred.provider_id,
            profile_id=cred.profile_id,
            credential_id=cred.credential_id,
            detail={"reason": reason, "message": detail},
        )

    status_for_reason: dict[str, CredentialStatus] = {
        "rate_limit": "rate_limited",
        "rate_limit_long": "rate_limited",
        "billing_blocked": "billing_blocked",
        "server_error": "valid",     # key itself is fine; just cooling off
        "network_error": "valid",
    }
    cooldown_for_reason: dict[str, int] = {
        "rate_limit": policy.rate_limit_cooldown_ms,
        "rate_limit_long": policy.rate_limit_long_cooldown_ms,
        "billing_blocked": policy.billing_cooldown_ms,
        "server_error": policy.server_error_cooldown_ms,
        "network_error": policy.network_error_cooldown_ms,
    }
    if reason not in status_for_reason:
        # Unknown reason — record it, but don't cool down for an
        # unbounded time. Default to the shortest known window.
        status_for_reason[reason] = "valid"
        cooldown_for_reason[reason] = policy.network_error_cooldown_ms

    cred.status = status_for_reason[reason]
    cred.cooldown_until_ms = now + cooldown_for_reason[reason]
    return AuthEvent(
        type=AuthEventType.POOL_MEMBER_COOLDOWN,
        provider_id=cred.provider_id,
        profile_id=cred.profile_id,
        credential_id=cred.credential_id,
        detail={
            "reason": reason,
            "message": detail,
            "cooldown_until_ms": cred.cooldown_until_ms,
            "cooldown_ms": cooldown_for_reason[reason],
        },
    )


def mark_success(cred: Credential, *, now_ms: Optional[int] = None) -> None:
    """Clear transient error state after a successful API call.

    Called by the manager on 2xx. Keeps ``use_count`` / ``last_used_at_ms``
    (those are updated in :func:`pick`), but clears any cooldown that
    might still be set from a prior failure whose cooldown window has
    already passed, and resets ``last_error``.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if cred.cooldown_until_ms and cred.cooldown_until_ms <= now:
        cred.cooldown_until_ms = 0
    if cred.status in ("rate_limited", "billing_blocked") and \
            (not cred.cooldown_until_ms or cred.cooldown_until_ms <= now):
        cred.status = "valid"
    cred.last_error = None


def clear_cooldown(cred: Credential) -> None:
    """Force-clear a cooldown. Used by "retry now" UI and in tests."""
    cred.cooldown_until_ms = 0
    if cred.status in ("rate_limited", "billing_blocked"):
        cred.status = "valid"


# ---------------------------------------------------------------------------
# Introspection — useful for the settings UI + debugging
# ---------------------------------------------------------------------------

@dataclass
class PoolHealth:
    """Read-only view of a pool's current operational state."""

    total: int
    healthy: int
    cooling_down: int
    needs_reauth: int
    revoked: int
    active_strategy: PoolStrategy
    next_cooldown_expires_at_ms: int   # 0 if nothing is cooling down


def health(pool: CredentialPool, *, now_ms: Optional[int] = None) -> PoolHealth:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cooling_down = 0
    needs_reauth = 0
    revoked = 0
    next_exp = 0
    for c in pool.credentials:
        if c.status == "needs_reauth":
            needs_reauth += 1
        elif c.status == "revoked":
            revoked += 1
        elif c.cooldown_until_ms and c.cooldown_until_ms > now:
            cooling_down += 1
            next_exp = c.cooldown_until_ms if next_exp == 0 else min(next_exp, c.cooldown_until_ms)
    healthy = len(_healthy_indices(pool, now))
    return PoolHealth(
        total=len(pool.credentials),
        healthy=healthy,
        cooling_down=cooling_down,
        needs_reauth=needs_reauth,
        revoked=revoked,
        active_strategy=pool.strategy,
        next_cooldown_expires_at_ms=next_exp,
    )


__all__ = [
    "DEFAULT_COOLDOWNS_MS",
    "PoolFailurePolicy",
    "pick",
    "mark_failure",
    "mark_success",
    "clear_cooldown",
    "PoolHealth",
    "health",
]
