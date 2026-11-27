"""Provider quota: headers become one model, kept in memory and written throttled.

Header shapes are OpenAI's ``x-ratelimit-*`` and Anthropic's API-key
``anthropic-ratelimit-*``. Nothing here reaches a provider.
"""
from __future__ import annotations

import pytest

from openprogram.models.providers.utils.http_transport.rate_limit import parse_rate_limit
from openprogram.models.usage import quota
from openprogram.models.usage.ledger import UsageLedger
from openprogram.models.usage.quota import QuotaLimit, QuotaStore

NOW = 1_800_000_000.0

OPENAI_HEADERS = {
    "x-ratelimit-limit-requests": "500",
    "x-ratelimit-remaining-requests": "499",
    "x-ratelimit-reset-requests": "120ms",
    "x-ratelimit-limit-tokens": "30000",
    "x-ratelimit-remaining-tokens": "3000",
    "x-ratelimit-reset-tokens": "6m0s",
}
ANTHROPIC_HEADERS = {
    "anthropic-ratelimit-requests-limit": "50",
    "anthropic-ratelimit-requests-remaining": "49",
    "anthropic-ratelimit-requests-reset": "2027-01-15T08:00:30Z",
    "anthropic-ratelimit-input-tokens-limit": "40000",
    "anthropic-ratelimit-input-tokens-remaining": "0",
    "anthropic-ratelimit-input-tokens-reset": "2027-01-15T08:00:30Z",
    "anthropic-ratelimit-output-tokens-limit": "8000",
    "anthropic-ratelimit-output-tokens-remaining": "7000",
}


def test_openai_headers_become_per_minute_headroom():
    limits = {limit.limit_key: limit for limit in quota.header_limits(OPENAI_HEADERS, now=NOW)}
    assert limits["rpm"].label == "Req/min" and limits["rpm"].remaining == 499
    assert limits["tpm"].used_fraction == pytest.approx(0.9)
    assert limits["tpm"].status == "warning"
    assert limits["tpm"].resets_at == pytest.approx(NOW + 360)
    assert limits["rpm"].resets_at == pytest.approx(NOW + 0.12)
    assert quota.report_kind(limits.values()) == "rate_limit"


def test_anthropic_api_key_headers_include_input_and_output_tokens():
    info = parse_rate_limit(ANTHROPIC_HEADERS)
    assert info.limit_input_tokens == 40000 and info.remaining_output_tokens == 7000
    limits = {limit.limit_key: limit for limit in quota.header_limits(ANTHROPIC_HEADERS, now=NOW)}
    assert set(limits) == {"rpm", "tpm_in", "tpm_out"}
    assert limits["tpm_in"].status == "exhausted"
    from datetime import datetime, timezone
    assert limits["rpm"].resets_at == datetime(2027, 1, 15, 8, 0, 30, tzinfo=timezone.utc).timestamp()


def test_a_reply_without_limit_headers_reports_nothing():
    assert quota.header_limits({"content-type": "text/event-stream"}, now=NOW) == []
    assert quota.report_kind([]) == "none"


@pytest.mark.parametrize("fraction, remaining, status", [
    (None, None, "unknown"), (0.5, None, "ok"), (0.8, None, "warning"), (1.0, None, "exhausted"),
    (0.1, 0.0, "exhausted"),
])
def test_thresholds(fraction, remaining, status):
    assert quota.limit_status(fraction, remaining) == status


def _window(fraction: float, minutes: int, resets_in: float) -> QuotaLimit:
    return QuotaLimit(limit_key="primary", label="5h", unit="percent", source="headers", fetched_at=NOW,
                      used_fraction=fraction, window_minutes=minutes, resets_at=NOW + resets_in)


def test_the_early_warning_is_time_aware():
    # 85% used with a tenth of the window left: behind the clock, quiet.
    assert not quota.should_warn(_window(0.85, 300, 30 * 60), now=NOW)
    # 85% used with most of the window left: ahead of time, warn.
    assert quota.should_warn(_window(0.85, 300, 240 * 60), now=NOW)
    # 95% warns whatever the time.
    assert quota.should_warn(_window(0.96, 300, 10 * 60), now=NOW)
    assert not quota.should_warn(_window(0.5, 300, 290 * 60), now=NOW)


def test_a_snapshot_older_than_fifteen_minutes_is_stale():
    limit = _window(0.2, 300, 3600)
    assert not limit.wire(NOW + 14 * 60)["stale"]
    assert limit.wire(NOW + 16 * 60)["stale"]


class _Ledger:
    def __init__(self) -> None:
        self.writes: list[list[dict]] = []
        self.dropped: list[tuple] = []

    def quota_rows(self, provider=None):
        return []

    def put_quota_rows(self, rows):
        self.writes.append(rows)

    def drop_quota_rows(self, provider, account, keys):
        self.dropped.append((provider, account, tuple(keys)))


def test_header_observations_reach_the_disk_at_most_every_thirty_seconds():
    ledger = _Ledger()
    store = QuotaStore(ledger)
    heard: list[dict] = []
    store.set_listener(heard.append)
    store.observe("openai", "cred-1", quota.header_limits(OPENAI_HEADERS, now=NOW))
    store.observe("openai", "cred-1", quota.header_limits(OPENAI_HEADERS, now=NOW + 5))
    assert len(ledger.writes) == 1
    store.observe("openai", "cred-1", quota.header_limits(OPENAI_HEADERS, now=NOW + 31))
    assert len(ledger.writes) == 2
    # The listener hears a change once, not every identical reply.
    assert len(heard) == 1 and heard[0]["provider"] == "openai" and heard[0]["kind"] == "rate_limit"
    # The latest value is in memory at once.
    (report,) = store.reports("openai")
    assert report.account == "cred-1" and report.fetched_at == NOW + 31


def test_an_endpoint_read_is_written_at_once_and_replaces_its_old_rows():
    ledger = _Ledger()
    store = QuotaStore(ledger)
    first = [QuotaLimit(limit_key=k, label=k, unit="usd", source="endpoint", fetched_at=NOW, remaining=1.0)
             for k in ("balance", "key_limit")]
    store.observe("openrouter", "", first, force_write=True, replace_source=True)
    second = [QuotaLimit(limit_key="key_limit", label="Key limit", unit="usd", source="endpoint",
                         fetched_at=NOW + 1, remaining=0.5)]
    store.observe("openrouter", "", second, force_write=True, replace_source=True)
    assert len(ledger.writes) == 2
    assert ledger.dropped == [("openrouter", "", ("balance",))]
    (report,) = store.reports("openrouter")
    assert [limit.limit_key for limit in report.limits] == ["key_limit"]
    assert report.kind == "balance"


def _plan_windows(now: float) -> list[QuotaLimit]:
    return [
        QuotaLimit(limit_key=key, label=key, unit="percent", source="headers", fetched_at=now,
                   used=used, limit=100.0, remaining=100.0 - used, used_fraction=used / 100.0,
                   window_minutes=300, resets_at=now + 3600)
        for key, used in (("primary", 12.5), ("secondary", 40.0), ("other:primary", 80.0))
    ]


def test_snapshots_survive_in_usage_db(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    store = QuotaStore(ledger)
    store.observe("zai", "cred-1", _plan_windows(NOW))
    again = QuotaStore(ledger)
    (report,) = again.reports("zai")
    assert {limit.limit_key for limit in report.limits} >= {"primary", "secondary"}
    assert report.worst().limit_key == "other:primary"
    # One sample per limit per fifteen minutes.
    store.observe("zai", "cred-1", _plan_windows(NOW + 60), force_write=True)
    samples = ledger.quota_samples(provider="zai", since=0)
    assert len([s for s in samples if s["limit_key"] == "primary"]) == 1
    store.observe("zai", "cred-1", _plan_windows(NOW + 16 * 60), force_write=True)
    samples = ledger.quota_samples(provider="zai", since=0)
    assert len([s for s in samples if s["limit_key"] == "primary"]) == 2
    ledger.close()


def test_a_token_is_matched_to_its_stored_credential_by_digest(tmp_path, monkeypatch):
    from openprogram.models.auth.store import AuthStore
    from openprogram.models.auth.types import Credential, CredentialData

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = AuthStore(root=tmp_path)
    monkeypatch.setattr("openprogram.models.auth.store._store", store)
    cred = store.add_credential(Credential(provider_id="openai", kind="api_key", payload=CredentialData(
        kind="api_key", auth_value="sk-test-1")))
    quota.forget_accounts()
    assert quota.account_for_token("openai", "sk-test-1") == cred.id
    assert quota.account_for_token("openai", "sk-other") == ""
    assert quota.account_for_token("openai", None) is None
    assert all("sk-test" not in key[1] for key in quota._account_cache)
