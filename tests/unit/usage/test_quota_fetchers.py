"""Quota endpoints: one interface, recorded shapes, and when a read is sent.

Payloads are the shapes the cited sources document (OpenProgram's own key
check for OpenRouter; Oh My Pi and ZCode for the unverified ones). The HTTP
call is a fake: nothing here reaches a provider.
"""
from __future__ import annotations

import json

import pytest

from openprogram.models.usage import quota, quota_fetchers
from openprogram.models.usage.quota import QuotaStore
from openprogram.models.usage.quota_fetchers import (
    FetchCredential,
    codex_usage,
    github_copilot,
    minimax,
    opencode_go,
    openrouter,
    zai,
)

NOW = 1_800_000_000.0


def test_openrouter_keeps_the_amount_left_not_a_yes_or_no():
    (limit,) = openrouter.parse({"data": {"limit": 20, "limit_remaining": 18.42, "usage": 1.58,
                                          "limit_reset": "monthly"}}, NOW)
    assert limit.limit_key == "key_limit" and limit.unit == "usd"
    assert limit.remaining == 18.42 and limit.limit == 20 and limit.label == "Key limit this month"
    assert limit.status == "ok"
    (spent,) = openrouter.parse({"data": {"limit": 5, "limit_remaining": 0, "usage": 5}}, NOW)
    assert spent.status == "exhausted"
    (unlimited,) = openrouter.parse({"data": {"limit": None, "limit_remaining": None, "usage": 3.2}}, NOW)
    assert unlimited.remaining is None and unlimited.used == 3.2 and unlimited.status == "ok"
    assert openrouter.parse({"error": "nope"}, NOW) == []


def test_codex_usage_payload():
    payload = {
        "plan_type": "pro",
        "rate_limit": {"primary_window": {"used_percent": 30, "limit_window_seconds": 18000,
                                          "reset_after_seconds": 600},
                       "secondary_window": {"used_percent": 71, "limit_window_seconds": 604800,
                                            "reset_at": NOW + 86400}},
        "credits": {"has_credits": True, "unlimited": False, "balance": "40"},
        "additional_rate_limits": [{"limit_name": "codex-other", "rate_limit": {
            "primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}}],
    }
    limits = {limit.limit_key: limit for limit in codex_usage.parse(payload, NOW)}
    assert limits["primary"].label == "5h" and limits["primary"].resets_at == NOW + 600
    assert limits["secondary"].label == "Weekly"
    assert limits["credits"].remaining == 40
    assert limits["codex_other:primary"].label == "codex-other 5h"
    assert all(limit.source == "endpoint" for limit in limits.values())


def test_zai_plan_windows():
    payload = {"data": {"limits": [
        {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 42, "nextResetTime": (NOW + 600) * 1000},
        {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 100},
    ]}}
    tokens, requests = zai.parse(payload, NOW)
    assert tokens.label == "5h" and tokens.used_fraction == pytest.approx(0.42)
    assert tokens.resets_at == pytest.approx(NOW + 600)
    assert requests.label == "Monthly requests" and requests.status == "exhausted"
    url, headers, _base = zai.request(FetchCredential("zai", "c", "zk"))
    assert url == "https://api.z.ai/api/monitor/usage/quota/limit"
    assert headers == {"Authorization": "zk"}


def test_minimax_token_plan():
    start = NOW * 1000
    payload = {"model_remains": [
        {"model_name": "general", "start_time": start, "end_time": start + 5 * 3600 * 1000,
         "current_interval_remaining_percent": 25, "current_interval_status": 1,
         "current_weekly_remaining_percent": 90, "current_weekly_status": 1},
        {"model_name": "video", "current_interval_total_count": 0, "current_weekly_total_count": 0,
         "current_interval_status": 3, "current_weekly_status": 3},
        {"model_name": "speech", "current_interval_status": 2, "current_weekly_remaining_percent": 50},
    ]}
    limits = {limit.limit_key: limit for limit in minimax.parse(payload, NOW)}
    assert limits["general:interval"].label == "5h"
    assert limits["general:interval"].used_fraction == pytest.approx(0.75)
    assert limits["general:weekly"].label == "Weekly"
    assert "video:interval" not in limits  # not in the plan
    assert limits["speech:interval"].status == "exhausted"


def test_opencode_go_windows():
    payload = {"usage": {"rolling": {"percent": 12, "status": "ok", "resetsAt": "2027-01-01T00:00:00Z"},
                         "weekly": {"percent": 100, "status": "rate-limited", "resetsAt": "2027-01-03T00:00:00Z"},
                         "monthly": {"percent": 3, "status": "ok", "resetsAt": "2027-01-20T00:00:00Z"}}}
    limits = {limit.limit_key: limit for limit in opencode_go.parse(payload, NOW)}
    assert limits["primary"].label == "5h" and limits["secondary"].status == "exhausted"
    assert limits["monthly"].window_minutes is None


def test_copilot_premium_requests():
    payload = {"copilot_plan": "pro", "quota_reset_date": "2027-02-01",
               "quota_snapshots": {"premium_interactions": {"entitlement": 300, "remaining": 60,
                                                            "percent_remaining": 20, "unlimited": False},
                                   "chat": {"entitlement": 0, "remaining": 0, "percent_remaining": 100,
                                            "unlimited": True}}}
    (premium,) = github_copilot.parse(payload, NOW)
    assert premium.used == 240 and premium.used_fraction == pytest.approx(0.8) and premium.status == "warning"
    assert quota.report_kind([premium]) == "windowed"


def test_only_verified_fetchers_are_sent():
    assert quota_fetchers.fetcher_for("openrouter") is openrouter
    for provider in ("openai-codex", "zai", "minimax", "opencode-go", "github-copilot", "deepseek"):
        assert quota_fetchers.fetcher_for(provider) is None
    listed = {row["provider"]: row["verified"] for row in quota_fetchers.sources()}
    assert listed["openrouter"] is True and listed["openai-codex"] is False
    assert "deepseek" not in listed and "moonshotai" not in listed


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A store on a temp ledger, one stored OpenRouter key, and a recording HTTP fake."""
    from openprogram.models.usage.ledger import UsageLedger

    store = QuotaStore(UsageLedger(db_path=tmp_path / "usage.db"))
    monkeypatch.setattr(quota_fetchers, "default_store", store)
    monkeypatch.setattr(quota_fetchers, "credentials",
                        lambda provider: [FetchCredential(provider, "cred-1", "sk-or-test")])
    sent: list[str] = []
    answers: list[tuple] = []

    def http_get(url, headers, base):
        sent.append(url)
        assert headers["Authorization"] == "Bearer sk-or-test"
        return answers.pop(0) if answers else (200, json.dumps({"data": {"limit": 10, "limit_remaining": 4,
                                                                          "usage": 6}}), {})

    from types import SimpleNamespace
    return SimpleNamespace(store=store, sent=sent, answers=answers, http_get=http_get)


def test_page_open_reads_only_values_older_than_ten_minutes(fake):
    assert quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW) == {"openrouter": 1}
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + 9 * 60)
    assert len(fake.sent) == 1
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + 11 * 60)
    assert len(fake.sent) == 2
    (report,) = fake.store.reports("openrouter")
    assert report.limits[0].remaining == 4


@pytest.mark.parametrize("answer", [(401, '{"error": "no key"}', {}), (500, "", {}), (200, "not json", {}), None])
def test_page_open_does_not_ask_a_refusing_endpoint_again_within_ten_minutes(fake, answer):
    for offset in (0, 60, 9 * 60):
        fake.answers.append(answer)
        quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + offset)
    assert len(fake.sent) == 1
    fake.answers.append(answer)
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + 11 * 60)
    assert len(fake.sent) == 2
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 11 * 60 + 1)
    assert len(fake.sent) == 3


def test_manual_and_key_added_reads_always_go(fake):
    for reason in ("manual", "key_added", "manual"):
        quota_fetchers.refresh(["openrouter"], reason=reason, http_get=fake.http_get, now=NOW)
    assert len(fake.sent) == 3


def test_a_failure_reads_once_per_ten_minutes(fake):
    for offset in (0, 60, 120):
        quota_fetchers.refresh(["openrouter"], reason="failure", http_get=fake.http_get, now=NOW + offset)
    assert len(fake.sent) == 1


def test_a_429_from_the_endpoint_holds_the_key(fake):
    fake.answers.append((429, "slow down", {"Retry-After": "120"}))
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW)
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 60)
    assert len(fake.sent) == 1
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 121)
    assert len(fake.sent) == 2
    fake.answers.append((429, "slow down", {}))
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 200)
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 200 + 299)
    assert len(fake.sent) == 3


def test_unverified_and_unknown_providers_send_nothing(fake):
    assert quota_fetchers.refresh(["openai-codex", "deepseek", "zai"], reason="manual",
                                  http_get=fake.http_get, now=NOW) == {}
    assert fake.sent == []


def test_after_failure_schedules_only_for_quota_failures(monkeypatch):
    scheduled: list[tuple] = []
    monkeypatch.setattr(quota_fetchers, "refresh_later",
                        lambda providers, reason: scheduled.append((tuple(providers), reason)))

    class Failed(Exception):
        def __init__(self, status, text=""):
            super().__init__(text)
            self.http_status = status

    quota_fetchers.after_failure("openrouter", Failed(402))
    quota_fetchers.after_failure("openrouter", Failed(None, "insufficient_quota"))
    quota_fetchers.after_failure("openrouter", Failed(500))
    quota_fetchers.after_failure("deepseek", Failed(402))  # nothing to read
    assert scheduled == [(("openrouter",), "failure"), (("openrouter",), "failure")]


def _new_process(monkeypatch, fake, tmp_path):
    """What a fresh CLI run or a restarted Worker has: nothing in memory, the same usage.db."""
    from openprogram.models.usage.ledger import UsageLedger

    quota_fetchers.reset_policy()
    fake.store = QuotaStore(UsageLedger(db_path=tmp_path / "usage.db"))
    monkeypatch.setattr(quota_fetchers, "default_store", fake.store)


@pytest.mark.parametrize("answer", [(401, '{"error": "no key"}', {}), (500, "", {}), None])
def test_a_refused_read_is_not_repeated_by_the_next_process(fake, monkeypatch, tmp_path, answer):
    fake.answers.append(answer)
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW)
    _new_process(monkeypatch, fake, tmp_path)
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + 60)
    assert len(fake.sent) == 1
    quota_fetchers.refresh(["openrouter"], reason="open", http_get=fake.http_get, now=NOW + 11 * 60)
    assert len(fake.sent) == 2


def test_a_429_hold_and_a_failure_read_outlive_the_process(fake, monkeypatch, tmp_path):
    fake.answers.append((429, "slow down", {"Retry-After": "120"}))
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW)
    _new_process(monkeypatch, fake, tmp_path)
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 60)
    assert len(fake.sent) == 1
    quota_fetchers.refresh(["openrouter"], reason="failure", http_get=fake.http_get, now=NOW + 200)
    _new_process(monkeypatch, fake, tmp_path)
    quota_fetchers.refresh(["openrouter"], reason="failure", http_get=fake.http_get, now=NOW + 260)
    assert len(fake.sent) == 2


def test_a_failed_read_keeps_the_values_and_names_why_once(fake, monkeypatch, tmp_path):
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW)
    fake.answers.append((401, '{"error": "no key"}', {}))
    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 60)
    _new_process(monkeypatch, fake, tmp_path)
    (report,) = fake.store.reports("openrouter")
    assert report.limits[0].remaining == 4 and report.fetched_at == NOW
    reads = quota_fetchers.last_reads("openrouter")
    assert reads["cred-1"]["error"] == "HTTP 401" and reads["cred-1"]["read_at"] == NOW + 60

    from openprogram.models.usage import provider_status
    key = {"account": "a", "credential_id": "cred-1", "kind": "api_key", "status": "valid", "active": True,
           "cooldown_until": None, "last_error": None, "last_used_at": None, "use_count": 0}
    wire = provider_status.quota_of([report], [key], NOW + 120, reads)
    assert wire["read_error"] == "HTTP 401" and wire["fetched_at"] == NOW
    health = provider_status.health_of([key], [report], NOW + 120, reads)
    assert health["state"] == "invalid_key" and "401" in health["detail"]

    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 180)
    reads = quota_fetchers.last_reads("openrouter")
    assert reads["cred-1"]["error"] is None
    assert provider_status.health_of([key], fake.store.reports("openrouter"), NOW + 200, reads)["state"] == "ready"
