"""Unit tests for auth.manager — refresh dedup + fallback chains."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable

import pytest

from openprogram.auth import (
    ApiKeyPayload,
    AuthConfigError,
    AuthEventType,
    AuthNeedsReauthError,
    AuthPoolExhaustedError,
    AuthReadOnlyError,
    AuthStore,
    Credential,
    OAuthPayload,
)
from openprogram.auth.manager import (
    AuthManager,
    ProviderAuthConfig,
    register_provider_config,
    get_provider_config,
)


def _oauth(
    provider="chatgpt-subscription",
    profile="default",
    access="A",
    refresh="R",
    expires_at_ms: int | None = None,
) -> Credential:
    if expires_at_ms is None:
        expires_at_ms = int(time.time() * 1000) + 3600_000
    return Credential(
        provider_id=provider, profile_id=profile, kind="oauth",
        payload=OAuthPayload(
            access_token=access, refresh_token=refresh,
            expires_at_ms=expires_at_ms, client_id="cid",
        ),
    )


def _api(provider="openai", profile="default", key="k") -> Credential:
    return Credential(
        provider_id=provider, profile_id=profile, kind="api_key",
        payload=ApiKeyPayload(api_key=key),
    )


def _manager(tmp_path: Path) -> tuple[AuthManager, AuthStore]:
    store = AuthStore(root=tmp_path)
    return AuthManager(store=store), store


# ---- happy path ------------------------------------------------------------

def test_acquire_returns_api_key_credential(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_api())
    cred = asyncio.run(m.acquire("openai"))
    assert cred.payload.api_key == "k"


def test_acquire_unknown_provider_raises_config_error(tmp_path: Path):
    m, _ = _manager(tmp_path)
    with pytest.raises(AuthConfigError):
        asyncio.run(m.acquire("unknown"))


def test_acquire_picks_healthy_from_pool(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_api(key="a"))
    store.add_credential(_api(key="b"))
    cred = asyncio.run(m.acquire("openai"))
    # fill_first default → picks "a"
    assert cred.payload.api_key == "a"


# ---- OAuth expiry + refresh -----------------------------------------------

def test_fresh_oauth_is_not_refreshed(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_oauth(access="A"))
    calls = []

    def refresh(c):
        calls.append(c)
        return _oauth(access="SHOULD_NOT_HAPPEN")

    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription", refresh=refresh,
    ))
    cred = asyncio.run(m.acquire("chatgpt-subscription"))
    assert cred.payload.access_token == "A"
    assert calls == []


def test_expired_oauth_triggers_refresh(tmp_path: Path):
    m, store = _manager(tmp_path)
    expired = _oauth(access="old", expires_at_ms=0)
    store.add_credential(expired)

    def refresh(c):
        return Credential(
            provider_id=c.provider_id, profile_id=c.profile_id,
            kind="oauth", credential_id=c.credential_id,
            payload=OAuthPayload(
                access_token="new",
                refresh_token=c.payload.refresh_token,
                expires_at_ms=int(time.time() * 1000) + 3600_000,
                client_id=c.payload.client_id,
            ),
        )

    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription", refresh=refresh,
    ))
    cred = asyncio.run(m.acquire("chatgpt-subscription"))
    assert cred.payload.access_token == "new"
    # Persisted back
    stored = store.get_pool("chatgpt-subscription", "default").credentials[0]
    assert stored.payload.access_token == "new"


def test_missing_refresh_fn_raises_needs_reauth(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_oauth(access="x", expires_at_ms=0))
    register_provider_config(ProviderAuthConfig(provider_id="chatgpt-subscription"))
    with pytest.raises(AuthNeedsReauthError):
        asyncio.run(m.acquire("chatgpt-subscription"))


# ---- concurrent refresh dedup ---------------------------------------------

def test_concurrent_refresh_is_deduped(tmp_path: Path):
    m, store = _manager(tmp_path)
    expired = _oauth(access="old", expires_at_ms=0)
    store.add_credential(expired)
    call_count = {"n": 0}

    async def async_refresh(c):
        call_count["n"] += 1
        # Simulate a slow refresh so all 10 acquires overlap.
        await asyncio.sleep(0.05)
        return Credential(
            provider_id=c.provider_id, profile_id=c.profile_id,
            kind="oauth", credential_id=c.credential_id,
            payload=OAuthPayload(
                access_token=f"new-{call_count['n']}",
                refresh_token="R2",
                expires_at_ms=int(time.time() * 1000) + 3600_000,
                client_id="cid",
            ),
        )

    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription", async_refresh=async_refresh,
    ))

    async def run():
        return await asyncio.gather(*(m.acquire("chatgpt-subscription") for _ in range(10)))

    results = asyncio.run(run())
    # Every caller got the same result from a single refresh.
    assert call_count["n"] == 1
    assert all(r.payload.access_token == "new-1" for r in results)


# ---- read-only -------------------------------------------------------------

def test_read_only_expired_credential_raises_read_only(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _oauth(access="x", expires_at_ms=0)
    cred.read_only = True
    store.add_credential(cred)
    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription",
        refresh=lambda c: c,   # would mutate if called; must not be called
    ))
    with pytest.raises(AuthReadOnlyError):
        asyncio.run(m.acquire("chatgpt-subscription"))


def test_read_only_fresh_credential_is_returned_asis(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _oauth(access="fresh")
    cred.read_only = True
    store.add_credential(cred)
    register_provider_config(ProviderAuthConfig(provider_id="chatgpt-subscription"))
    out = asyncio.run(m.acquire("chatgpt-subscription"))
    assert out.payload.access_token == "fresh"


# ---- fallback chain --------------------------------------------------------

def test_fallback_chain_takes_over_when_primary_missing(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_api(provider="anthropic", key="ant"))
    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription",
        fallback_chain=[("anthropic", "default")],
    ))
    cred = asyncio.run(m.acquire("chatgpt-subscription"))
    assert cred.provider_id == "anthropic"
    assert cred.payload.api_key == "ant"


def test_fallback_chain_cycle_is_broken(tmp_path: Path):
    m, _ = _manager(tmp_path)
    register_provider_config(ProviderAuthConfig(
        provider_id="a", fallback_chain=[("b", "default")],
    ))
    register_provider_config(ProviderAuthConfig(
        provider_id="b", fallback_chain=[("a", "default")],
    ))
    with pytest.raises(AuthConfigError):
        asyncio.run(m.acquire("a"))


def test_pool_exhausted_falls_through_to_next(tmp_path: Path):
    m, store = _manager(tmp_path)
    # Primary pool: revoked
    dead = _api(provider="chatgpt-subscription", key="dead")
    dead.status = "revoked"
    store.add_credential(dead)
    # Fallback: healthy api key on anthropic
    store.add_credential(_api(provider="anthropic", key="ant"))
    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription",
        fallback_chain=[("anthropic", "default")],
    ))
    cred = asyncio.run(m.acquire("chatgpt-subscription"))
    assert cred.provider_id == "anthropic"


def test_pool_exhausted_with_no_fallback_raises(tmp_path: Path):
    m, store = _manager(tmp_path)
    dead = _api(provider="chatgpt-subscription", key="dead")
    dead.status = "revoked"
    store.add_credential(dead)
    register_provider_config(ProviderAuthConfig(provider_id="chatgpt-subscription"))
    with pytest.raises(AuthPoolExhaustedError):
        asyncio.run(m.acquire("chatgpt-subscription"))


# ---- failure reporting -----------------------------------------------------

def test_report_failure_cools_down_credential(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _api(key="k")
    store.add_credential(cred)
    m.report_failure("openai", "default", cred.credential_id, "rate_limit")
    reloaded = store.get_pool("openai", "default").credentials[0]
    assert reloaded.cooldown_until_ms > int(time.time() * 1000)
    assert reloaded.status == "rate_limited"


def test_report_success_clears_expired_cooldown(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _api(key="k")
    cred.cooldown_until_ms = 1   # in the past
    cred.status = "rate_limited"
    store.add_credential(cred)
    m.report_success("openai", "default", cred.credential_id)
    # In-memory is cleared; disk write skipped on purpose, but the
    # status transition we care about happened.
    assert cred.status == "valid"


# ---- events ---------------------------------------------------------------

def test_refresh_emits_started_and_succeeded(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_oauth(access="x", expires_at_ms=0))
    register_provider_config(ProviderAuthConfig(
        provider_id="chatgpt-subscription",
        refresh=lambda c: Credential(
            provider_id=c.provider_id, profile_id=c.profile_id,
            kind="oauth", credential_id=c.credential_id,
            payload=OAuthPayload(
                access_token="new", refresh_token="r",
                expires_at_ms=int(time.time() * 1000) + 3600_000,
                client_id="cid",
            ),
        ),
    ))
    events = []
    store.subscribe(events.append)
    asyncio.run(m.acquire("chatgpt-subscription"))
    types = {e.type for e in events}
    assert AuthEventType.REFRESH_STARTED in types
    assert AuthEventType.REFRESH_SUCCEEDED in types


def test_pool_exhausted_emits_event(tmp_path: Path):
    m, store = _manager(tmp_path)
    dead = _api(provider="x", key="k"); dead.status = "revoked"
    store.add_credential(dead)
    register_provider_config(ProviderAuthConfig(provider_id="x"))
    events = []
    store.subscribe(events.append)
    with pytest.raises(AuthPoolExhaustedError):
        asyncio.run(m.acquire("x"))
    assert any(e.type == AuthEventType.POOL_EXHAUSTED for e in events)
