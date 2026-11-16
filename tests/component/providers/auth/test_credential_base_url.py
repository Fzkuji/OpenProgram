"""A credential's own base_url and headers reach the adapter: the lease a
request is sent with (``opts.credential``, models/auth/selection.py) carries
them, so a stored key (e.g. Aliyun Bailian) can point at its own endpoint
instead of the provider's default."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.auth import selection
from openprogram.models.providers.types import Model, SimpleStreamOptions


@pytest.fixture
def store(tmp_path):
    from openprogram.models.auth.credential_provider import CredentialProvider, set_credential_provider_for_testing
    from openprogram.models.auth.store import AuthStore, set_store_for_testing

    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    set_credential_provider_for_testing(CredentialProvider(store=s))
    selection.forget_bindings()
    yield s
    set_store_for_testing(None)
    set_credential_provider_for_testing(None)


def _lease_for(provider: str):
    seen = []

    async def attempt(opts):
        seen.append(opts)
        if False:
            yield None

    async def drain():
        model = Model(id="m", name="m", api="openai-completions", provider=provider,
                      base_url="https://api.example/v1")
        async for _ in selection.with_credential(model, SimpleStreamOptions(), attempt):
            pass

    asyncio.run(drain())
    return seen[0]


def _add(store, provider, value, **payload):
    from openprogram.models.auth.types import Credential, CredentialData
    store.add_credential(Credential(
        provider_id=provider, kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=value, **payload)))


def test_the_lease_carries_the_credential_base_url(store):
    _add(store, "openai-compat", "sk-bailian-key", base_url="https://bailian/v1",
         headers={"X-Org": "team"})
    opts = _lease_for("openai-compat")
    assert opts.api_key == "sk-bailian-key"
    assert opts.credential.base_url == "https://bailian/v1"
    assert opts.credential.headers == {"X-Org": "team"}


def test_no_base_url_leaves_the_model_default(store):
    _add(store, "plain", "sk-plain-000001")
    assert _lease_for("plain").credential.base_url is None


def test_a_provider_without_credentials_is_passed_through(store):
    opts = _lease_for("no-such-provider")
    assert opts.api_key is None and getattr(opts, "credential", None) is None
