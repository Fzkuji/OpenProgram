"""Concurrent requests that find a sign-in near expiry spend its refresh
token once (openprogram/models/auth/credential_provider.py)."""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.models.auth import AuthStore, Credential, CredentialData
from openprogram.models.auth.credential_provider import (
    CredentialProvider,
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.models.auth.store import set_store_for_testing

P = "refresh-once-test"


@pytest.fixture
def store(tmp_path):
    from openprogram.models.auth import credential_provider as cp
    value = AuthStore(root=tmp_path)
    set_store_for_testing(value)
    yield value
    set_store_for_testing(None)
    cp._provider_configs.pop(P, None)


def _oauth(access, expires_at_ms):
    return Credential(provider_id=P, kind="oauth", payload=CredentialData(
        kind="oauth", auth_value=access,
        data={"refresh_token": "R", "expires_at_ms": expires_at_ms, "client_id": "cid"}))


def _renewed(c, value):
    return Credential(provider_id=c.provider_id, kind="oauth", id=c.id, payload=CredentialData(
        kind="oauth", auth_value=value,
        data={"refresh_token": "R2", "expires_at_ms": int(time.time() * 1000) + 3600_000, "client_id": "cid"}))


def test_concurrent_refreshes_spend_the_token_once(store):
    m = CredentialProvider(store=store)
    cred = store.add_credential(_oauth("old", 0))
    calls = {"n": 0}
    entered = threading.Event()
    release = threading.Event()

    def refresh(c):
        calls["n"] += 1
        entered.set()
        release.wait(5)
        return _renewed(c, f"new-{calls['n']}")

    register_provider_config(ProviderAuthConfig(provider_id=P, refresh=refresh))
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(m.prepare(cred).payload.auth_value))
               for _ in range(8)]
    for t in threads:
        t.start()
    entered.wait(5)
    release.set()
    for t in threads:
        t.join(5)
    assert calls["n"] == 1
    assert results == ["new-1"] * 8
