"""A conversation stays on the credential that served it.

Under a rotating strategy every request would otherwise go to the next key,
and what the vendor cached of the conversation under the last key is paid
for again in full. The pin holds while the credential is healthy and the
conversation was active within the cache's lifetime.
"""

from __future__ import annotations

import pytest

from openprogram.models.auth import pool as pool_module
from openprogram.models.auth.context import conversation_affinity, get_affinity_key
from openprogram.models.auth.credential_provider import CredentialProvider
from openprogram.models.auth.store import AuthStore
from openprogram.models.auth.types import Credential, CredentialData, CredentialPool


def _key(value: str) -> Credential:
    return Credential(
        provider_id="openai", account_id="default", kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=value),
    )


def _pool(strategy: str = "round_robin") -> CredentialPool:
    return CredentialPool(
        provider_id="openai", account_id="default", strategy=strategy,
        credentials=[_key("k1"), _key("k2"), _key("k3")],
    )


@pytest.fixture(autouse=True)
def _fresh_memory():
    pool_module.forget_affinity()
    yield
    pool_module.forget_affinity()


def _value(cred: Credential) -> str:
    return cred.payload.auth_value


@pytest.mark.parametrize("strategy", ["round_robin", "least_used", "random"])
def test_a_conversation_keeps_its_credential(strategy):
    pool = _pool(strategy)
    first = _value(pool_module.pick(pool, now_ms=1_000, affinity_key="chat-a"))
    for step in range(5):
        assert _value(pool_module.pick(pool, now_ms=2_000 + step, affinity_key="chat-a")) == first


def test_conversations_still_spread_across_credentials():
    pool = _pool()
    picks = {_value(pool_module.pick(pool, now_ms=1_000, affinity_key=f"chat-{n}")) for n in range(3)}
    assert picks == {"k1", "k2", "k3"}


def test_requests_without_a_conversation_rotate_as_before():
    pool = _pool()
    assert [_value(pool_module.pick(pool, now_ms=1_000)) for _ in range(3)] == ["k1", "k2", "k3"]


def test_a_cooling_credential_lets_the_conversation_move_and_stay_moved():
    pool = _pool()
    first = pool_module.pick(pool, now_ms=1_000, affinity_key="chat-a")
    first.cooldown_until_ms = 10_000
    moved = pool_module.pick(pool, now_ms=2_000, affinity_key="chat-a")
    assert moved.credential_id != first.credential_id
    assert pool_module.pick(pool, now_ms=20_000, affinity_key="chat-a").credential_id == moved.credential_id


def test_the_pin_lapses_once_the_cache_has_gone_cold():
    pool = _pool()
    first = pool_module.pick(pool, now_ms=0, affinity_key="chat-a")
    later = pool_module.AFFINITY_IDLE_MS + 1
    # round_robin's cursor now points past ``first``.
    assert pool_module.pick(pool, now_ms=later, affinity_key="chat-a").credential_id != first.credential_id


def test_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(pool_module, "AFFINITY_MAX", 2)
    pool = _pool()
    for n in range(5):
        pool_module.pick(pool, now_ms=1_000, affinity_key=f"chat-{n}")
    assert len(pool_module._affinity) == 2


def test_the_ambient_conversation_reaches_the_pool(tmp_path):
    store = AuthStore(root=tmp_path)
    store.put_pool(_pool())
    provider = CredentialProvider(store=store)
    with conversation_affinity("chat-a"):
        assert get_affinity_key() == "chat-a"
        first = _value(provider.acquire_sync("openai", "default"))
        again = [_value(provider.acquire_sync("openai", "default")) for _ in range(3)]
    assert get_affinity_key() == ""
    assert again == [first] * 3
