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
    deepseek,
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


def test_minimax_reads_the_plan_of_the_token_plan_providers():
    # The shape of a recorded response: an unused plan.
    start = 1794936124659
    payload = {"model_remains": [{
        "start_time": start, "end_time": start + 18000000, "remains_time": 17999990,
        "current_interval_total_count": 0, "current_interval_usage_count": 0, "model_name": "general",
        "current_weekly_total_count": 0, "current_weekly_usage_count": 0,
        "weekly_start_time": start, "weekly_end_time": start + 604800000, "weekly_remains_time": 604799990,
        "current_interval_status": 1, "current_interval_remaining_percent": 100,
        "current_weekly_status": 1, "current_weekly_remaining_percent": 100,
    }], "base_resp": {"status_code": 0, "status_msg": "success"}}
    limits = {limit.limit_key: limit for limit in minimax.parse(payload, NOW)}
    assert limits["general:interval"].label == "5h" and limits["general:interval"].used_fraction == 0
    assert limits["general:weekly"].resets_at == pytest.approx((start + 604800000) / 1000)
    # A key the plan does not know answers 200 with no plan in it.
    assert minimax.parse({"base_resp": {"status_code": 2049, "status_msg": "invalid api key"}}, NOW) == []
    url, _, _ = minimax.request(FetchCredential("minimax-cn-coding-plan", "k", "sk"))
    assert url == "https://api.minimaxi.com/v1/token_plan/remains"
    assert quota_fetchers.fetcher_for("minimax-coding-plan") is minimax
    assert quota_fetchers.fetcher_for("minimax") is None, "pay as you go has no plan to read"


def test_opencode_go_windows():
    # A recorded response.
    payload = {"usage": {"rolling": {"status": "ok", "percent": 0, "resetsAt": "2026-11-17T22:21:19.475Z"},
                         "weekly": {"status": "ok", "percent": 13, "resetsAt": "2026-11-23T00:00:00.000Z"},
                         "monthly": {"status": "rate-limited", "percent": 100, "resetsAt": "2026-11-28T19:23:26.000Z"}}}
    limits = {limit.limit_key: limit for limit in opencode_go.parse(payload, NOW)}
    assert limits["primary"].label == "5h" and limits["secondary"].used_fraction == pytest.approx(0.13)
    assert limits["monthly"].status == "exhausted" and limits["monthly"].window_minutes is None


def test_deepseek_balance():
    # A recorded response: an account that owes, which cannot pay for a call.
    payload = {"is_available": False, "balance_infos": [
        {"currency": "CNY", "total_balance": "-0.26", "granted_balance": "0.00", "topped_up_balance": "-0.26"}]}
    (balance,) = deepseek.parse(payload, NOW)
    assert (balance.limit_key, balance.unit, balance.remaining, balance.status) == ("balance", "cny", -0.26, "exhausted")
    assert quota.report_kind([balance]) == "balance"
    two = deepseek.parse({"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00"}, {"currency": "USD", "total_balance": "5.50"}]}, NOW)
    assert [(l.limit_key, l.remaining, l.status) for l in two] == [("cny:balance", 110.0, "ok"), ("usd:balance", 5.5, "ok")]
    assert deepseek.parse({"error": "nope"}, NOW) == []


def test_codex_usage_of_a_spent_week_with_credits():
    # The shape of a recorded response (identity fields left out).
    payload = {"plan_type": "prolite", "rate_limit": {"allowed": False, "limit_reached": True, "primary_window": {
        "used_percent": 100, "limit_window_seconds": 604800, "reset_after_seconds": 147604, "reset_at": 1795083766},
        "secondary_window": None}, "additional_rate_limits": None,
        "credits": {"has_credits": True, "unlimited": False, "balance": "26871.6297055000"}}
    limits = {limit.limit_key: limit for limit in codex_usage.parse(payload, NOW)}
    assert limits["primary"].label == "Weekly" and limits["primary"].status == "exhausted"
    assert limits["credits"].remaining == pytest.approx(26871.6297055)


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
    for provider in ("openai-codex", "opencode-go", "deepseek", "minimax-cn-coding-plan"):
        assert quota_fetchers.fetcher_for(provider) is not None
    for provider in ("zai", "github-copilot"):
        assert quota_fetchers.fetcher_for(provider) is None
    listed = {row["provider"]: row["verified"] for row in quota_fetchers.sources()}
    assert listed["openrouter"] is True and listed["zai"] is False
    assert "moonshotai" not in listed


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
    assert quota_fetchers.refresh(["github-copilot", "moonshotai", "zai"], reason="manual",
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
    quota_fetchers.after_failure("moonshotai", Failed(402))  # nothing to read
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
    key = {"account": "a", "credential_id": "cred-1", "kind": "api_key", "status": "ready", "active": True,
           "cooldown_until": None, "last_error": None, "last_used_at": None, "use_count": 0}
    wire = provider_status.quota_of([report], [key], NOW + 120, reads)
    assert wire["read_error"] == "HTTP 401" and wire["fetched_at"] == NOW
    health = provider_status.health_of([key], [report], NOW + 120, reads)
    assert health["state"] == "invalid_key" and "401" in health["detail"]

    quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 180)
    reads = quota_fetchers.last_reads("openrouter")
    assert reads["cred-1"]["error"] is None
    assert provider_status.health_of([key], fake.store.reports("openrouter"), NOW + 200, reads)["state"] == "ready"


def test_a_key_its_quota_endpoint_refuses_is_marked_on_its_row(fake, tmp_path):
    from openprogram.models.auth import Credential, CredentialData
    from openprogram.models.auth.store import AuthStore, set_store_for_testing

    auth = AuthStore(root=tmp_path / "state")
    set_store_for_testing(auth)
    try:
        cred = auth.add_credential(Credential(provider_id="openrouter", kind="api_key", source="cli",
                                              payload=CredentialData(kind="api_key", auth_value="sk-or-test")))
        quota_fetchers.credentials = lambda provider: [FetchCredential(provider, cred.id, "sk-or-test")]
        fake.answers.append((401, '{"error": "no key"}', {}))
        quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW)
        row = auth.find("openrouter", cred.id)
        assert row.status == "invalid_key"
        assert "401" in (row.last_error or {}).get("message", "")

        from openprogram.models.auth import selection
        marked = []
        mark = selection.mark
        selection.mark = lambda *args, **kwargs: marked.append(args)
        try:
            fake.answers.append((401, '{"error": "no key"}', {}))
            quota_fetchers.refresh(["openrouter"], reason="manual", http_get=fake.http_get, now=NOW + 700)
        finally:
            selection.mark = mark
        assert len(fake.sent) == 2, "the endpoint is still asked: the key may have been put right"
        assert marked == [], "a key already marked refused is not marked, and announced, again"
    finally:
        set_store_for_testing(None)


@pytest.mark.parametrize("module, body", [
    (minimax, '{"error": "no token plan"}'),  # a plan's endpoint, which does not speak for the key
    (openrouter, '{"type": "error", "error": {"type": "CreditsError", "message": "Insufficient balance"}}'),
])
def test_a_401_that_does_not_say_the_key_is_refused_leaves_its_row(fake, tmp_path, module, body):
    from openprogram.models.auth import Credential, CredentialData
    from openprogram.models.auth.store import AuthStore, set_store_for_testing

    auth = AuthStore(root=tmp_path / "state")
    set_store_for_testing(auth)
    try:
        cred = auth.add_credential(Credential(provider_id="minimax", kind="api_key", source="cli",
                                              payload=CredentialData(kind="api_key", auth_value="sk-or-test")))
        fake.answers.append((401, body, {}))
        quota_fetchers.fetch_one(module, FetchCredential("minimax", cred.id, "sk-or-test"),
                                 http_get=fake.http_get, now=NOW)
        assert auth.find("minimax", cred.id).status == "ready"
        assert quota_fetchers.last_reads("minimax")[cred.id]["error"] == "HTTP 401"
    finally:
        set_store_for_testing(None)
