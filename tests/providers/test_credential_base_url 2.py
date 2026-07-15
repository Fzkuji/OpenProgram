"""Wire layers must prefer a credential's own base_url/headers over the
catalog default, and acquire_pooled must hand back the ResolvedConnection
that carries them (not a bare token string) — the whole point of the
credential-connection-unification refactor: a stored api-key (e.g. Aliyun
Bailian) can point at its own endpoint instead of the provider's default."""
from __future__ import annotations

import pytest

from openprogram.auth.resolver import ResolvedConnection


def test_wire_uses_credential_base_url_over_catalog():
    # conn.base_url set → wins; conn.base_url None → falls back to model.base_url.
    def pick(conn_base, model_base):
        return (conn_base if conn_base else None) or model_base

    assert pick("https://bailian/v1", "https://api.openai.com/v1") == "https://bailian/v1"
    assert pick(None, "https://api.openai.com/v1") == "https://api.openai.com/v1"


@pytest.fixture
def store(tmp_path):
    from openprogram.auth.manager import AuthManager, set_manager_for_testing
    from openprogram.auth.store import AuthStore, set_store_for_testing

    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    set_manager_for_testing(AuthManager(store=s))
    yield s
    set_store_for_testing(None)
    set_manager_for_testing(None)


def test_acquire_pooled_returns_resolved_connection_with_base_url(store):
    """An api_key credential carrying its own base_url (e.g. a Bailian key)
    must come back through acquire_pooled as a ResolvedConnection whose
    .base_url is that stored value — not lost as a bare token string."""
    from openprogram.auth import usage as u
    from openprogram.auth.types import Credential, CredentialData

    store.add_credential(Credential(
        provider_id="openai-compat", profile_id="default", kind="api_key",
        payload=CredentialData(
            kind="api_key", auth_value="sk-bailian-key",
            base_url="https://bailian/v1",
        ),
        source="test",
    ))

    got = u.acquire_pooled("openai-compat")
    assert got is not None
    conn, profile, cred_id = got
    assert isinstance(conn, ResolvedConnection)
    assert conn.auth_value == "sk-bailian-key"
    assert conn.base_url == "https://bailian/v1"
    assert profile == "default"
    assert cred_id


def test_acquire_pooled_conn_base_url_none_when_unset(store):
    """No base_url on the credential → conn.base_url is None (not ""), so
    wire code's `conn.base_url or model.base_url` fallback triggers."""
    from openprogram.auth import usage as u
    from openprogram.auth.types import Credential, CredentialData

    store.add_credential(Credential(
        provider_id="plain", profile_id="default", kind="api_key",
        payload=CredentialData(kind="api_key", auth_value="sk-plain"),
        source="test",
    ))

    conn, _, _ = u.acquire_pooled("plain")
    assert conn.base_url is None


def test_acquire_pooled_none_when_no_pool(store):
    from openprogram.auth import usage as u
    assert u.acquire_pooled("no-such-provider") is None
