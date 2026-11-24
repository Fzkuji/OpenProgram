"""What the model picker, the provider detail chart and the quota-exhausted card read."""
from __future__ import annotations

import pytest

from openprogram.models.usage import provider_status, provider_views, quota
from openprogram.models.usage.event import UsageEvent
from openprogram.models.usage.ledger import UsageLedger
from openprogram.models.usage.quota import QuotaLimit

NOW = 1_800_000_000.0
DAY = 86400


def _window(used: float, *, key: str = "primary", label: str = "5h", minutes: int = 300,
            resets_in: float = 3600) -> QuotaLimit:
    return QuotaLimit(limit_key=key, label=label, unit="percent", source="headers", fetched_at=NOW,
                      used=used, limit=100.0, remaining=100.0 - used, used_fraction=used / 100,
                      window_minutes=minutes, resets_at=NOW + resets_in)


@pytest.mark.parametrize("used, tone, ring", [
    (10, "normal", False), (50, "normal", True), (80, "warning", True), (95, "danger", True),
    (100, "danger", True),
])
def test_the_picker_ring_follows_the_bar_rule(used, tone, ring):
    picked = provider_views.picker_quota([_window(used)], NOW)
    assert picked["tone"] == tone and picked["ring"] is ring
    assert picked["left_fraction"] == pytest.approx(1 - used / 100)


def test_the_picker_shows_the_window_closest_to_running_out_and_skips_per_minute_limits():
    rpm = QuotaLimit(limit_key="rpm", label="Req/min", unit="requests", source="headers", fetched_at=NOW,
                     limit=100, remaining=1, window_minutes=1)
    picked = provider_views.picker_quota([_window(30), _window(70, key="secondary", label="Weekly",
                                                                minutes=10080), rpm], NOW)
    assert picked["label"] == "Weekly"
    assert provider_views.picker_quota([rpm], NOW) is None


def test_the_early_warning_is_time_aware():
    # 85 % used with a quarter of the window left: usage is behind the clock, so no warning.
    assert provider_views.picker_quota([_window(85, resets_in=0.1 * 300 * 60)], NOW)["warn"] is False
    # 85 % used with most of the window still ahead: it warns.
    assert provider_views.picker_quota([_window(85, resets_in=0.9 * 300 * 60)], NOW)["warn"] is True
    assert provider_views.picker_quota([_window(96, resets_in=10)], NOW)["warn"] is True


@pytest.fixture
def store(tmp_path, monkeypatch):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    store = quota.QuotaStore(ledger)
    monkeypatch.setattr(provider_views, "default_store", store)
    monkeypatch.setattr(provider_status, "default_store", store)
    from openprogram.models.usage import quota_fetchers
    monkeypatch.setattr(quota_fetchers, "last_reads", lambda provider: {})
    yield store
    ledger.close()


def _key(status: str = "ready", cred: str = "cred-1") -> dict:
    return {"account": "default", "credential_id": cred, "kind": "api_key", "status": status, "active": True,
            "cooldown_until": None, "last_error": None, "last_used_at": NOW - 60, "use_count": 1,
            "status_at": NOW - 60}


def test_a_provider_without_a_key_says_so_and_leads_to_connect(store, monkeypatch):
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [])
    state = provider_views.picker_state("deepseek", configured=False, now=NOW)
    assert state["state"] == "not_configured" and state["attention"] is True
    assert state["fix"] == {"action": "connect", "url": "https://platform.deepseek.com/api_keys"}
    assert state["name"] is None and state["name_zh"] is None


def test_opening_the_picker_probes_a_local_server_again(store, monkeypatch):
    """A local server announces nothing when it starts or stops: background
    readers reuse a recent probe, the picker opening asks it now."""
    from openprogram.models.listing import detected
    asked: list[float] = []
    running = {"now": False}

    def probe(provider_id, base_url=None, *, max_age=0.0):
        asked.append(max_age)
        return running["now"]

    monkeypatch.setattr(detected, "local_server_running", probe)
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [])
    assert provider_views.picker_state("ollama", configured=True, now=NOW)["state"] == "not_running"
    running["now"] = True
    state = provider_views.picker_state("ollama", configured=True, now=NOW, probe_local=True)
    assert state["state"] == "ready"
    assert asked == [provider_views.LOCAL_PROBE_MAX_AGE_S, 0.0]
    assert provider_views.picker_states(["ollama"], probe_local=True)["ollama"]["state"] == "ready"
    assert asked[-1] == 0.0


def test_one_way_of_a_company_carries_its_menu_name(store, monkeypatch):
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [])
    state = provider_views.picker_state("openai-codex", configured=False, now=NOW)
    assert state["name"] == "OpenAI · ChatGPT"
    assert provider_views.picker_state("opencode-go", configured=False, now=NOW)["name"] == "OpenCode · Go"


def test_a_provider_out_of_credit_shows_its_balance_and_the_top_up_page(store, monkeypatch):
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [_key("quota")])
    store.observe("openrouter", "cred-1", [QuotaLimit(
        limit_key="key_limit", label="Key limit", unit="usd", source="endpoint", fetched_at=NOW - 30,
        limit=20, remaining=0)], force_write=True)
    state = provider_views.picker_state("openrouter", configured=True, now=NOW)
    assert state["state"] == "no_credit"
    assert state["fix"] == {"action": "top_up", "url": "https://openrouter.ai/settings/credits"}
    assert state["balance"] == {"amount": 0.0, "unit": "usd", "label": "Key limit"}
    assert state["quota"]["tone"] == "danger" and state["attention"] is True


def test_a_quiet_provider_draws_nothing(store, monkeypatch):
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [_key()])
    store.observe("openai-codex", "cred-1", [_window(20)], force_write=True)
    state = provider_views.picker_state("openai-codex", configured=True, now=NOW)
    assert state["state"] == "ready" and state["fix"] is None and state["attention"] is False
    assert state["quota"]["ring"] is False and state["balance"] is None


def test_a_warning_quota_leaves_the_trigger_plain_until_nearly_spent(store, monkeypatch):
    monkeypatch.setattr(provider_views, "keys_of", lambda provider: [_key()])
    # 84 % used and ahead of the window's time: the bars warn, the trigger stays plain.
    store.observe("openai-codex", "cred-1", [_window(84, resets_in=270 * 60)], force_write=True)
    state = provider_views.picker_state("openai-codex", configured=True, now=NOW)
    assert state["quota"]["tone"] == "warning" and state["quota"]["warn"] is True
    assert state["attention"] is False
    store.observe("openai-codex", "cred-1", [_window(96)], force_write=True)
    state = provider_views.picker_state("openai-codex", configured=True, now=NOW)
    assert state["quota"]["tone"] == "danger" and state["attention"] is True


def _event(provider: str, model: str, day: int, cost: float, *, source: str = "model_catalog") -> UsageEvent:
    return UsageEvent(ts=NOW - day * DAY, session_id="s", call_kind="chat", provider=provider, model_id=model,
                      input_tokens=100, output_tokens=10, cache_read_tokens=900, total_tokens=110,
                      cost_total=cost, cost_source=source)


def test_usage_by_day_and_model(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    ledger.append_many([_event("deepseek", "flash", 0, 0.5), _event("deepseek", "pro", 0, 1.0),
                        _event("deepseek", "flash", 3, 0.25, source="unknown"), _event("openai", "x", 0, 9)])
    usage = provider_views.provider_usage("deepseek", days=7, group="day,model", ledger=ledger, now=NOW)
    assert len(usage["series"]) == 7 and usage["series"][-1]["calls"] == 2
    assert usage["series"][-1]["models"]["pro"] == {"list_price_usd": 1.0, "tokens": 1010}
    assert usage["totals"]["calls"] == 3 and usage["totals"]["unpriced_calls"] == 1
    # Old rows whose total left the cache out read as the sum of their parts.
    assert usage["totals"]["total_tokens"] == 3 * 1010
    assert usage["totals"]["cost_usd"] == pytest.approx(1.75)
    assert [m["model_id"] for m in usage["models"]] == ["pro", "flash"]
    with pytest.raises(ValueError):
        provider_views.provider_usage("deepseek", group="week", ledger=ledger, now=NOW)
    ledger.close()


def _enabled(provider: str, model: str, *, ctx: int = 128_000, vision: bool = False) -> dict:
    return {"provider": provider, "provider_label": provider.title(), "id": model, "name": model,
            "context_window": ctx, "tools": True, "vision": vision, "reasoning": True}


def _ready(*ids: str, spent: tuple[str, ...] = ()) -> dict:
    out = {}
    for pid in ids:
        out[pid] = {"id": pid, "configured": True, "state": "ready", "until": None, "fix": None,
                    "quota": {"left_fraction": 0.0} if pid in spent else None, "balance": None,
                    "stale": False, "attention": False}
    return out


def test_the_fallback_prefers_the_same_model_elsewhere(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    enabled = [_enabled("kimi-coding", "kimi-k2"), _enabled("openrouter", "moonshotai/kimi-k2"),
               _enabled("deepseek", "flash")]
    got = provider_views.fallback_for("kimi-coding", "kimi-k2", enabled=enabled,
                                      states=_ready("openrouter", "deepseek"), ledger=ledger, now=NOW)
    assert got["suggestion"]["provider"] == "openrouter" and got["suggestion"]["reason"] == "same_model"
    assert [a["model"] for a in got["alternatives"]] == ["flash"]
    ledger.close()


def test_the_fallback_takes_the_most_used_healthy_model_that_fits(tmp_path):
    ledger = UsageLedger(db_path=tmp_path / "usage.db")
    ledger.append_many([_event("deepseek", "flash", 1, 0.1), _event("deepseek", "flash", 1, 0.1),
                        _event("xai", "grok", 1, 0.1)])
    enabled = [_enabled("openai-codex", "gpt"), _enabled("xai", "grok"), _enabled("deepseek", "flash"),
               _enabled("mistral", "small", ctx=32_000), _enabled("google", "gemini", vision=True),
               _enabled("zai", "glm")]
    got = provider_views.fallback_for(
        "openai-codex", "gpt", context_tokens=100_000, enabled=enabled,
        states=_ready("xai", "deepseek", "mistral", "google", spent=("xai",)), ledger=ledger, now=NOW)
    assert got["suggestion"]["model"] == "flash" and got["suggestion"]["reason"] == "most_used"
    # Spent (xai), too small a window (mistral), other capabilities (google), unknown health (zai): left out.
    assert got["alternatives"] == []
    ledger.close()
