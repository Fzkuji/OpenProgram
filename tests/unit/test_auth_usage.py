"""Tests for auth/usage.py — the pool failure/success reporting that makes
multi-key rotation + cooldown actually engage in the provider call path."""
from __future__ import annotations

import pytest

from openprogram.auth import usage
from openprogram.auth.manager import AuthManager, set_manager_for_testing
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import ApiKeyPayload, Credential


@pytest.fixture
def store(tmp_path):
    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    set_manager_for_testing(AuthManager(store=s))
    yield s
    set_store_for_testing(None)
    set_manager_for_testing(None)


def _seed(store, provider, profile, keys):
    for k in keys:
        store.add_credential(Credential(
            provider_id=provider, profile_id=profile, kind="api_key",
            payload=ApiKeyPayload(api_key=k), source="test",
        ))


def test_acquire_pooled_none_when_no_pool(store):
    assert usage.acquire_pooled("no-such-provider") is None


def test_acquire_pooled_returns_token_profile_credid(store):
    _seed(store, "rot", "default", ["KEY-A"])
    got = usage.acquire_pooled("rot")
    assert got is not None
    token, profile, cred_id = got
    assert token == "KEY-A"
    assert profile == "default"
    assert cred_id  # non-empty


def test_report_failure_cools_down_and_rotates(store):
    _seed(store, "rot", "default", ["KEY-A", "KEY-B"])
    # fill_first → first acquire is KEY-A
    tok1, prof1, id1 = usage.acquire_pooled("rot")
    assert tok1 == "KEY-A"
    # a 429 on KEY-A cools it down …
    usage.report_failure("rot", prof1, id1, status=429, error_text="429 Too Many Requests")
    # … so the next acquire rotates to KEY-B
    tok2, _, id2 = usage.acquire_pooled("rot")
    assert tok2 == "KEY-B"
    assert id2 != id1
    # both cooled → no healthy credential left → None (caller falls back)
    usage.report_failure("rot", "default", id2, status=402, error_text="billing")
    assert usage.acquire_pooled("rot") is None


def test_fixed_uses_active_key_and_does_not_failover(store):
    _seed(store, "fx", "default", ["KEY-A", "KEY-B"])
    pool = store.find_pool("fx", "default")
    ids = [c.credential_id for c in pool.credentials]
    # rotation OFF: pin KEY-B as the active key
    pool.strategy = "fixed"
    pool.active_credential_id = ids[1]
    store.put_pool(pool)
    tok, _, cid = usage.acquire_pooled("fx")
    assert tok == "KEY-B"
    assert cid == ids[1]
    # cooling the pinned key changes nothing — "fixed" means fixed (no failover)
    usage.report_failure("fx", "default", ids[1], status=429)
    tok2, _, _ = usage.acquire_pooled("fx")
    assert tok2 == "KEY-B"


def test_rotation_on_fails_over_from_the_active_key(store):
    _seed(store, "fx2", "default", ["KEY-A", "KEY-B"])
    pool = store.find_pool("fx2", "default")
    ids = [c.credential_id for c in pool.credentials]
    pool.strategy = "fill_first"
    pool.active_credential_id = ids[1]  # KEY-B is the default/first try
    store.put_pool(pool)
    assert usage.acquire_pooled("fx2")[0] == "KEY-B"          # active tried first
    usage.report_failure("fx2", "default", ids[1], status=429)
    assert usage.acquire_pooled("fx2")[0] == "KEY-A"          # cooled → fail over


def test_pick_account_rotates_and_skips_cooled(store):
    # Two accounts = two profiles, one credential each.
    _seed(store, "rp", "default", ["KEY-A"])
    _seed(store, "rp", "backup", ["KEY-B"])
    pools = [p for p in store.list_pools() if p.provider_id == "rp"]
    # round-robin alternates across the accounts
    picks = [usage._pick_account("rp", pools, "round_robin").profile_id for _ in range(4)]
    assert set(picks) == {"default", "backup"}
    assert picks[0] != picks[1]
    # cool the default account's credential → it's skipped
    import time
    pool = store.find_pool("rp", "default")
    pool.credentials[0].cooldown_until_ms = int(time.time() * 1000) + 60_000
    store.put_pool(pool)
    pools = [p for p in store.list_pools() if p.provider_id == "rp"]
    chosen = usage._pick_account("rp", pools, "fill_first")
    assert chosen.profile_id == "backup"


def test_report_success_is_safe_noop_without_credential(store):
    # empty credential id must not raise (the non-pool provider path)
    usage.report_success("rot", "default", "")
    usage.report_failure("rot", "default", "", status=429)


@pytest.mark.parametrize("status,expected", [
    (429, "rate_limit"),
    (402, "billing_blocked"),
    (401, "needs_reauth"),
    (403, "needs_reauth"),
    # request/model-level 4xx — the key is fine, no cooldown
    (400, "request_error"),
    (404, "request_error"),
    (422, "request_error"),
    (500, "server_error"),
    (503, "server_error"),
    (None, "server_error"),
])
def test_classify_failure_by_status(status, expected):
    assert usage.classify_failure(status) == expected


def test_request_error_does_not_cool_the_credential(monkeypatch):
    """A 404 (model not found / not free) must NOT touch the pool —
    cooling the key would punish every other model on it."""
    called = []

    class _FakeManager:
        def report_failure(self, *a, **k):
            called.append(a)

    monkeypatch.setattr(
        "openprogram.auth.manager.get_manager", lambda: _FakeManager()
    )
    usage.report_failure("openrouter", "default", "cred-1", status=404,
                         error_text="This model is unavailable for free")
    assert called == []


@pytest.mark.parametrize("text,expected", [
    ("Rate limit exceeded", "rate_limit"),
    ("HTTP 429 too many requests", "rate_limit"),
    ("insufficient_quota / billing", "billing_blocked"),
    ("invalid api key", "needs_reauth"),
    ("Connection timeout", "network_error"),
    ("weird unknown error", "server_error"),
])
def test_classify_failure_by_text(text, expected):
    assert usage.classify_failure(None, text) == expected
