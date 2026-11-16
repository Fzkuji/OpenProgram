"""Making a chosen credential ready to send: refresh near expiry, one
refresh at a time, a dead refresh token marks it ``needs_login``
(openprogram/models/auth/credential_provider.py)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.models.auth import (
    AuthConfigError,
    AuthEventType,
    AuthNeedsReauthError,
    AuthReadOnlyError,
    AuthStore,
    Credential,
    CredentialData,
)
from openprogram.models.auth.credential_provider import (
    CredentialProvider,
    ProviderAuthConfig,
    register_provider_config,
)

P = "refresh-test"


@pytest.fixture(autouse=True)
def _forget_config():
    from openprogram.models.auth import credential_provider as cp
    yield
    cp._provider_configs.pop(P, None)


def _oauth(access="A", refresh="R", expires_at_ms: int | None = None) -> Credential:
    if expires_at_ms is None:
        expires_at_ms = int(time.time() * 1000) + 3600_000
    return Credential(
        provider_id=P, kind="oauth",
        payload=CredentialData(
            kind="oauth", auth_value=access,
            data={"refresh_token": refresh, "expires_at_ms": expires_at_ms, "client_id": "cid"},
        ),
    )


def _api(provider="openai", key="k") -> Credential:
    return Credential(provider_id=provider, kind="api_key",
                      payload=CredentialData(kind="api_key", auth_value=key))


def _renewed(c: Credential, value: str) -> Credential:
    return Credential(
        provider_id=c.provider_id, kind="oauth", id=c.id,
        payload=CredentialData(kind="oauth", auth_value=value, data={
            "refresh_token": "R2", "expires_at_ms": int(time.time() * 1000) + 3600_000,
            "client_id": "cid"}),
    )


def _manager(tmp_path: Path) -> tuple[CredentialProvider, AuthStore]:
    store = AuthStore(root=tmp_path)
    _use(store)
    return CredentialProvider(store=store), store


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    from openprogram.models.auth.store import set_store_for_testing
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield
    set_store_for_testing(None)


def _use(store):
    from openprogram.models.auth.store import set_store_for_testing
    set_store_for_testing(store)


# ---- choosing outside a conversation -----------------------------------------

def test_acquire_returns_the_first_key(tmp_path: Path):
    m, store = _manager(tmp_path)
    store.add_credential(_api(key="a"))
    store.add_credential(_api(key="b"))
    assert m.acquire_sync("openai").payload.auth_value == "a"


def test_acquire_for_a_provider_with_nothing_raises(tmp_path: Path):
    m, store = _manager(tmp_path)
    with pytest.raises(AuthConfigError):
        m.acquire_sync("unknown")


# ---- refresh ------------------------------------------------------------------

def test_a_fresh_sign_in_is_not_refreshed(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(access="A"))
    calls = []
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: calls.append(c)))
    assert m.prepare(cred).payload.auth_value == "A"
    assert calls == []


def test_an_expired_sign_in_is_refreshed_and_saved_in_place(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(access="old", expires_at_ms=0))
    store.update(P, cred.id, lambda c: setattr(c, "label", "Mine"))
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: _renewed(c, "new")))
    assert m.prepare(cred).payload.auth_value == "new"
    stored = store.get(P).credentials[0]
    assert (stored.id, stored.label, stored.payload.auth_value) == (cred.id, "Mine", "new")


def test_without_a_refresh_it_needs_signing_in(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(expires_at_ms=0))
    register_provider_config(ProviderAuthConfig(provider_id=P))
    with pytest.raises(AuthNeedsReauthError):
        m.prepare(cred)


def test_a_dead_refresh_token_marks_it_needs_login(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(expires_at_ms=0))

    def refresh(c):
        raise RuntimeError("OAuth refresh failed 400: invalid_grant, Refresh token not found or invalid")

    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=refresh))
    with pytest.raises(RuntimeError):
        m.prepare(cred)
    stored = store.get(P).credentials[0]
    assert stored.status == "needs_login" and stored.last_error


def test_a_transport_failure_leaves_it_as_it_was(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(expires_at_ms=0))

    def refresh(c):
        raise RuntimeError("OAuth refresh failed 503: connection timeout")

    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=refresh))
    with pytest.raises(RuntimeError):
        m.prepare(cred)
    assert store.get(P).credentials[0].status == "ready"


def test_a_sign_in_again_clears_needs_login(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(expires_at_ms=0))
    store.update(P, cred.id, lambda c: setattr(c, "status", "needs_login"))
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: _renewed(c, "new")))
    m.prepare(store.find(P, cred.id))
    assert store.get(P).credentials[0].status == "ready"


def test_a_forced_refresh_runs_before_expiry(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(access="old"))
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: _renewed(c, "forced")))
    assert m.force_refresh_sync(cred).payload.auth_value == "forced"


# ---- another tool's login -------------------------------------------------------

def test_an_expired_read_only_login_is_refused(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _oauth(expires_at_ms=0)
    cred.read_only = True
    cred = store.add_credential(cred)
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: pytest.fail("refreshed")))
    with pytest.raises(AuthReadOnlyError):
        m.prepare(cred)


def test_a_fresh_read_only_login_is_returned_as_is(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = _oauth(access="fresh")
    cred.read_only = True
    cred = store.add_credential(cred)
    register_provider_config(ProviderAuthConfig(provider_id=P))
    assert m.prepare(cred).payload.auth_value == "fresh"
    assert m.force_refresh_sync(cred).payload.auth_value == "fresh"


def test_a_view_is_what_the_request_sends(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(access="stored"))

    def view(c):
        return _renewed(c, "read-from-its-file")

    register_provider_config(ProviderAuthConfig(provider_id=P, view=view))
    assert m.prepare(cred).payload.auth_value == "read-from-its-file"


# ---- events ---------------------------------------------------------------------

def test_refresh_emits_started_and_succeeded(tmp_path: Path):
    m, store = _manager(tmp_path)
    cred = store.add_credential(_oauth(expires_at_ms=0))
    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=lambda c: _renewed(c, "new")))
    events = []
    store.subscribe(events.append)
    m.prepare(cred)
    types = {e.type for e in events}
    assert {AuthEventType.REFRESH_STARTED, AuthEventType.REFRESH_SUCCEEDED} <= types
