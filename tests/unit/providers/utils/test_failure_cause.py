"""A failed reply says which limit or refusal stopped it, and when a spent quota resets."""
from __future__ import annotations

import json

import pytest

from openprogram.models.providers.utils.errors import (
    CAUSE_INVALID_KEY,
    CAUSE_NOT_AVAILABLE,
    CAUSE_QUOTA,
    CAUSE_UNREACHABLE,
    ErrorReason,
    LLMError,
    failure_cause,
)
from openprogram.models.providers.utils.stream_retry import ProviderStreamError

NOW = 1_800_000_000.0


def _http(status: int, body: str, **extra) -> ProviderStreamError:
    return ProviderStreamError(f"HTTP {status}: {body}", http_status=status, error_text=body,
                               provider="openai-codex", **extra)


def test_a_spent_codex_plan_names_its_reset():
    body = json.dumps({"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached",
                                 "plan_type": "plus", "resets_in_seconds": 3600}})
    cause = failure_cause(_http(429, body), now=NOW)
    assert cause["cause"] == CAUSE_QUOTA
    assert cause["resets_at"] == NOW + 3600
    assert cause["http_status"] == 429 and cause["provider"] == "openai-codex"


def test_the_spent_window_is_named_from_the_last_quota_seen():
    from openprogram.models.usage import quota

    quota.default_store.observe("openai-codex", "", quota.header_limits({
        "x-codex-primary-used-percent": "100", "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": str(int(NOW + 900)),
    }, now=NOW))
    cause = failure_cause(_http(429, '{"error": {"type": "usage_limit_reached"}}'), now=NOW)
    assert cause["limit"] == "5h" and cause["resets_at"] == NOW + 900


def test_402_and_insufficient_quota_are_a_spent_quota():
    assert failure_cause(_http(402, "Payment required"))["cause"] == CAUSE_QUOTA
    assert failure_cause(_http(429, '{"error": {"code": "insufficient_quota"}}'))["cause"] == CAUSE_QUOTA
    retry = failure_cause(_http(402, "no credit", retry_after_s=60), now=NOW)
    assert retry["resets_at"] == NOW + 60


def test_an_ordinary_429_is_not_a_cause():
    assert failure_cause(_http(429, "Too many requests")) is None


def test_a_rejected_key():
    assert failure_cause(_http(401, "Unauthorized"))["cause"] == CAUSE_INVALID_KEY
    assert failure_cause(RuntimeError("Error code: 400 invalid x-api-key"))["cause"] == CAUSE_INVALID_KEY


def test_a_region_or_plan_refusal():
    body = '{"error": {"code": "unsupported_country_region_territory"}}'
    assert failure_cause(_http(403, body))["cause"] == CAUSE_NOT_AVAILABLE
    assert failure_cause(_http(403, '{"error": "forbidden"}'))["cause"] == CAUSE_NOT_AVAILABLE
    assert failure_cause(_http(404, "model not available on your plan"))["cause"] == CAUSE_NOT_AVAILABLE


def test_a_block_page_from_the_network_edge_stays_blocked():
    page = "<!doctype html><html><body>Access denied</body></html>"
    assert failure_cause(_http(403, page)) is None


def test_an_unreachable_host():
    class ConnectError(Exception):
        pass

    try:
        try:
            raise ConnectError("[Errno 8] nodename nor servname provided, or not known")
        except ConnectError as inner:
            raise RuntimeError("Connection error.") from inner
    except RuntimeError as outer:
        assert failure_cause(outer)["cause"] == CAUSE_UNREACHABLE
    assert failure_cause(ConnectionRefusedError("Connection refused"))["cause"] == CAUSE_UNREACHABLE


def test_a_mid_stream_break_is_none_of_them():
    assert failure_cause(_http(500, "internal")) is None
    assert failure_cause(TimeoutError("timed out")) is None


@pytest.mark.parametrize("cause", [{"cause": CAUSE_QUOTA, "limit": "Weekly", "resets_at": NOW}])
def test_a_reraised_failure_keeps_its_cause(cause):
    err = LLMError(message="provider returned an error", reason=ErrorReason.AUTHORIZATION, failure=cause)
    assert failure_cause(err) == cause


def test_a_spent_window_names_its_key_and_the_other_windows(monkeypatch):
    from openprogram.models.usage import quota

    store = quota.QuotaStore(ledger=type("NoDisk", (), {
        "quota_rows": lambda self: [], "put_quota_rows": lambda self, rows: None,
        "drop_quota_rows": lambda self, *a: None})())
    monkeypatch.setattr(quota, "default_store", store)
    store.observe("openai-codex", "cred-7", quota.header_limits({
        "x-codex-primary-used-percent": "100", "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": str(int(NOW + 900)),
        "x-codex-secondary-used-percent": "29", "x-codex-secondary-window-minutes": "10080",
        "x-codex-secondary-reset-at": str(int(NOW + 86400)),
    }, now=NOW))
    cause = failure_cause(_http(429, '{"error": {"type": "usage_limit_reached"}}'), now=NOW)
    assert cause["limit"] == "5h" and cause["account"] == "cred-7"
    assert cause["other_limits"] == [{"label": "Weekly", "left_fraction": 0.71, "resets_at": NOW + 86400}]


def test_a_401_credits_error_is_a_spent_quota_not_a_rejected_key():
    """OpenCode Zen answers a zero balance with 401 CreditsError: the reply card is QUOTA."""
    from openprogram.models.providers.utils.errors import classify_error
    from openprogram.models.providers.utils.failover import should_failover

    body = ('{"type": "error", "error": {"type": "CreditsError", '
            '"message": "Insufficient balance. Manage your billing here: https://opencode.ai/billing"}}')
    exc = _http(401, body)
    assert failure_cause(exc)["cause"] == CAUSE_QUOTA
    assert classify_error(exc, http_status=401, error_text=body) == (ErrorReason.AUTHORIZATION, False)
    assert should_failover(exc) is False
    assert classify_error(RuntimeError("Unauthorized: insufficient balance")) == (ErrorReason.AUTHORIZATION, False)
    assert classify_error(_http(401, "Unauthorized"), http_status=401) == (ErrorReason.AUTHENTICATION, False)
