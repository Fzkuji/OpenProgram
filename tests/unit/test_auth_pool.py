"""Unit tests for openprogram.auth.pool."""
from __future__ import annotations

import time
from collections import Counter

import pytest

from openprogram.auth import (
    ApiKeyPayload,
    AuthEventType,
    AuthPoolExhaustedError,
    Credential,
    CredentialPool,
)
from openprogram.auth.pool import (
    PoolFailurePolicy,
    clear_cooldown,
    health,
    mark_failure,
    mark_success,
    pick,
)


def _pool(*keys: str, strategy="fill_first") -> CredentialPool:
    creds = [
        Credential(
            provider_id="p", profile_id="d", kind="api_key",
            payload=ApiKeyPayload(api_key=k),
        )
        for k in keys
    ]
    return CredentialPool(provider_id="p", profile_id="d", strategy=strategy, credentials=creds)


# ---- pick: strategies ------------------------------------------------------

def test_fill_first_sticks_to_head():
    p = _pool("a", "b", "c")
    assert pick(p).payload.api_key == "a"
    assert pick(p).payload.api_key == "a"
    assert pick(p).payload.api_key == "a"


def test_fill_first_falls_over_when_head_cools_down():
    p = _pool("a", "b")
    mark_failure(p.credentials[0], "rate_limit")
    assert pick(p).payload.api_key == "b"


def test_round_robin_cycles_through_all():
    p = _pool("a", "b", "c", strategy="round_robin")
    seen = [pick(p).payload.api_key for _ in range(6)]
    assert seen == ["a", "b", "c", "a", "b", "c"]


def test_round_robin_skips_cooled_down():
    p = _pool("a", "b", "c", strategy="round_robin")
    mark_failure(p.credentials[1], "rate_limit")
    seen = [pick(p).payload.api_key for _ in range(4)]
    assert "b" not in seen


def test_random_stays_within_healthy_set():
    p = _pool("a", "b", "c", strategy="random")
    mark_failure(p.credentials[2], "rate_limit")
    seen = set(pick(p).payload.api_key for _ in range(50))
    assert "c" not in seen
    assert seen == {"a", "b"}


def test_least_used_prefers_lower_count():
    p = _pool("a", "b", strategy="least_used")
    # Artificially bump 'a' so 'b' wins.
    p.credentials[0].use_count = 10
    assert pick(p).payload.api_key == "b"


def test_pick_raises_when_pool_exhausted():
    p = _pool("a", "b")
    for c in p.credentials:
        mark_failure(c, "rate_limit")
    with pytest.raises(AuthPoolExhaustedError):
        pick(p)


def test_pick_ignores_revoked_and_needs_reauth():
    p = _pool("a", "b", "c", strategy="round_robin")
    mark_failure(p.credentials[0], "revoked")
    mark_failure(p.credentials[1], "needs_reauth")
    for _ in range(5):
        assert pick(p).payload.api_key == "c"


def test_pick_updates_usage_bookkeeping():
    p = _pool("a")
    before = p.credentials[0].last_used_at_ms
    c = pick(p)
    assert c.use_count == 1
    assert c.last_used_at_ms >= before


# ---- mark_failure: cooldowns ----------------------------------------------

def test_rate_limit_sets_cooldown():
    p = _pool("a")
    ev = mark_failure(p.credentials[0], "rate_limit",
                      policy=PoolFailurePolicy(rate_limit_cooldown_ms=5000),
                      now_ms=1000)
    assert p.credentials[0].status == "rate_limited"
    assert p.credentials[0].cooldown_until_ms == 6000
    assert ev.type == AuthEventType.POOL_MEMBER_COOLDOWN
    assert ev.detail["reason"] == "rate_limit"


def test_billing_sets_longer_cooldown():
    p = _pool("a")
    ev = mark_failure(p.credentials[0], "billing_blocked",
                      policy=PoolFailurePolicy(billing_cooldown_ms=3600_000),
                      now_ms=0)
    assert p.credentials[0].status == "billing_blocked"
    assert p.credentials[0].cooldown_until_ms == 3600_000


def test_revoked_is_permanent():
    p = _pool("a")
    ev = mark_failure(p.credentials[0], "revoked", detail="admin killed it")
    assert p.credentials[0].status == "revoked"
    assert p.credentials[0].cooldown_until_ms == 0
    assert ev.type == AuthEventType.REVOKED


def test_needs_reauth_emits_its_own_event():
    p = _pool("a")
    ev = mark_failure(p.credentials[0], "needs_reauth")
    assert p.credentials[0].status == "needs_reauth"
    assert ev.type == AuthEventType.NEEDS_REAUTH


def test_unknown_reason_gets_short_cooldown():
    p = _pool("a")
    ev = mark_failure(p.credentials[0], "cosmic_ray", now_ms=0)
    assert p.credentials[0].cooldown_until_ms > 0


# ---- cooldown expiry ------------------------------------------------------

def test_expired_cooldown_allows_pick_again():
    p = _pool("a", "b")
    mark_failure(p.credentials[0], "rate_limit",
                 policy=PoolFailurePolicy(rate_limit_cooldown_ms=10))
    time.sleep(0.02)
    # Cooldown in the past → credential is healthy again, fill_first picks it.
    c = pick(p)
    assert c.payload.api_key == "a"


def test_mark_success_clears_expired_cooldown_state():
    p = _pool("a")
    mark_failure(p.credentials[0], "rate_limit", now_ms=0)
    # Still in cooldown window
    mark_success(p.credentials[0], now_ms=1)
    assert p.credentials[0].status == "rate_limited"   # not yet expired
    mark_success(p.credentials[0], now_ms=10**12)       # far future → expired
    assert p.credentials[0].status == "valid"
    assert p.credentials[0].cooldown_until_ms == 0


def test_clear_cooldown_bypasses_timer():
    p = _pool("a")
    mark_failure(p.credentials[0], "rate_limit")
    clear_cooldown(p.credentials[0])
    assert p.credentials[0].cooldown_until_ms == 0
    # Picks cleanly now
    assert pick(p).payload.api_key == "a"


# ---- health ---------------------------------------------------------------

def test_health_reports_mixed_state():
    p = _pool("a", "b", "c", "d", strategy="round_robin")
    mark_failure(p.credentials[0], "rate_limit", now_ms=0,
                 policy=PoolFailurePolicy(rate_limit_cooldown_ms=1000))
    mark_failure(p.credentials[1], "revoked")
    mark_failure(p.credentials[2], "needs_reauth")
    state = health(p, now_ms=500)
    assert state.total == 4
    assert state.healthy == 1
    assert state.cooling_down == 1
    assert state.revoked == 1
    assert state.needs_reauth == 1
    assert state.next_cooldown_expires_at_ms == 1000


# ---- round-robin robustness ----------------------------------------------

def test_round_robin_uniform_over_long_run():
    p = _pool("a", "b", "c", strategy="round_robin")
    hits = Counter(pick(p).payload.api_key for _ in range(300))
    assert hits == {"a": 100, "b": 100, "c": 100}


def test_round_robin_survives_midstream_cooldown():
    p = _pool("a", "b", "c", strategy="round_robin")
    seen = []
    for i in range(6):
        if i == 2:
            # Cooldown relative to actual wall clock so pick() sees it.
            mark_failure(p.credentials[2], "rate_limit",
                         policy=PoolFailurePolicy(rate_limit_cooldown_ms=10_000))
        seen.append(pick(p).payload.api_key)
    assert "c" not in seen[3:]
