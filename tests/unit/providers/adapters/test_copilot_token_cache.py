"""Tests for the Copilot api_token cache."""
from __future__ import annotations

import time

import pytest

from openprogram.providers.github_copilot import token_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with an empty cache and the default exchange fn."""
    token_cache.clear_all()
    token_cache.set_exchange_fn(None)
    yield
    token_cache.clear_all()
    token_cache.set_exchange_fn(None)


def _fake_exchange_factory(counter: dict, *, ttl: int = 1800, token_prefix: str = "cop_"):
    def fake(github_oauth: str, base_url=None):
        counter["calls"] = counter.get("calls", 0) + 1
        return token_cache.CopilotApiToken(
            token=f"{token_prefix}{counter['calls']}",
            expires_at_epoch=int(time.time()) + ttl,
        )
    return fake


def test_first_call_exchanges(tmp_path):
    counter: dict = {}
    token_cache.set_exchange_fn(_fake_exchange_factory(counter))
    result = token_cache.get_copilot_api_token("gho_abc")
    assert result == "cop_1"
    assert counter["calls"] == 1


def test_second_call_hits_cache():
    counter: dict = {}
    token_cache.set_exchange_fn(_fake_exchange_factory(counter))
    token_cache.get_copilot_api_token("gho_abc")
    token_cache.get_copilot_api_token("gho_abc")
    assert counter["calls"] == 1


def test_different_oauth_tokens_are_cached_separately():
    counter: dict = {}
    token_cache.set_exchange_fn(_fake_exchange_factory(counter))
    token_cache.get_copilot_api_token("gho_a")
    token_cache.get_copilot_api_token("gho_b")
    assert counter["calls"] == 2


def test_force_refresh_skips_cache():
    counter: dict = {}
    token_cache.set_exchange_fn(_fake_exchange_factory(counter))
    token_cache.get_copilot_api_token("gho_abc")
    token_cache.get_copilot_api_token("gho_abc", force_refresh=True)
    assert counter["calls"] == 2


def test_expiring_cache_entry_re_exchanges():
    counter: dict = {}
    # TTL of 30s means is_expired() returns True (skew is 60s).
    token_cache.set_exchange_fn(_fake_exchange_factory(counter, ttl=30))
    first = token_cache.get_copilot_api_token("gho_abc")
    second = token_cache.get_copilot_api_token("gho_abc")
    assert first == "cop_1"
    assert second == "cop_2"
    assert counter["calls"] == 2


def test_invalidate_drops_entry():
    counter: dict = {}
    token_cache.set_exchange_fn(_fake_exchange_factory(counter))
    token_cache.get_copilot_api_token("gho_abc")
    token_cache.invalidate("gho_abc")
    token_cache.get_copilot_api_token("gho_abc")
    assert counter["calls"] == 2


def test_malformed_response_raises():
    def bad_exchange(_token, _base_url):
        # Simulate the http exchange path rejecting a malformed payload.
        raise RuntimeError("malformed response")
    token_cache.set_exchange_fn(bad_exchange)
    with pytest.raises(RuntimeError, match="malformed"):
        token_cache.get_copilot_api_token("gho_abc")


def test_empty_token_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        token_cache.get_copilot_api_token("")


def test_is_expired_honors_skew():
    # Expiry 30s from now — still "expired" under a 60s skew.
    tok = token_cache.CopilotApiToken(
        token="t", expires_at_epoch=int(time.time()) + 30,
    )
    assert tok.is_expired()
    tok2 = token_cache.CopilotApiToken(
        token="t", expires_at_epoch=int(time.time()) + 3600,
    )
    assert not tok2.is_expired()
