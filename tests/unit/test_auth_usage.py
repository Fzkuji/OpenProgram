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


def test_report_success_is_safe_noop_without_credential(store):
    # empty credential id must not raise (the non-pool provider path)
    usage.report_success("rot", "default", "")
    usage.report_failure("rot", "default", "", status=429)


@pytest.mark.parametrize("status,expected", [
    (429, "rate_limit"),
    (402, "billing_blocked"),
    (401, "needs_reauth"),
    (403, "needs_reauth"),
    (500, "server_error"),
    (503, "server_error"),
    (None, "server_error"),
])
def test_classify_failure_by_status(status, expected):
    assert usage.classify_failure(status) == expected


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
