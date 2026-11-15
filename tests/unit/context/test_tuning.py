"""The compaction share and cache lifetime replayed on the user's own calls
(runtime/context/tuning.py) and the two settings they advise."""
from __future__ import annotations

import pytest

from openprogram.runtime.context import tuning
from openprogram.runtime.context.engine import DefaultContextEngine
from openprogram.runtime.context.tuning import (
    ANTHROPIC_WEIGHTS,
    Call,
    advise_cache,
    advise_compaction,
    observe,
    streams,
)
from openprogram.models.providers.settings.cache_spec import with_lifetime_setting
from openprogram.state import settings

WINDOW = 200_000


def _weigh(model, api):
    return ANTHROPIC_WEIGHTS


def _conversation(name, *, calls, start=8_000, grow=3_000, gap=30.0, compact_at=None,
                  api="anthropic-messages"):
    out, prompt = [], start
    for i in range(calls):
        out.append(Call(ts=i * gap, stream=name, model="anthropic/claude-x", api=api,
                        prompt=prompt, out=500))
        prompt += grow
        if compact_at and prompt > compact_at:
            prompt = start + 12_000
    return out


def test_observe_reads_compactions_growth_and_gaps():
    f = observe(streams(_conversation("a", calls=80, compact_at=150_000)))
    assert f.calls == 80 and f.conversations == 1
    assert f.compactions >= 1
    assert f.growth == 3_000
    assert f.after == 20_000
    assert f.quick == 1.0 and f.away == 0.0


def test_a_conversation_that_grows_long_is_advised_to_compact_sooner():
    calls = _conversation("a", calls=120, compact_at=180_000) + _conversation(
        "b", calls=120, compact_at=180_000)
    advice = advise_compaction(calls, window=WINDOW, current=95, weigh=_weigh)

    assert advice is not None
    assert [p.percent for p in advice.curve] == sorted(p.percent for p in advice.curve)
    assert advice.best < 95
    assert advice.low < advice.cost
    assert (advice.cost - advice.low) / advice.cost >= tuning.SAME_COST


def test_a_setting_that_saves_little_is_left_as_it_is():
    calls = _conversation("a", calls=60, grow=200)  # never near a window
    advice = advise_compaction(calls, window=WINDOW, current=80, weigh=_weigh)

    assert advice is not None and advice.best == 80 and advice.low == advice.cost


def test_too_few_calls_say_nothing():
    assert advise_compaction(_conversation("a", calls=10), window=WINDOW, current=80) is None
    assert advise_cache(_conversation("a", calls=10), current="5m") is None


def test_coming_back_after_a_pause_is_advised_an_hours_cache():
    away = _conversation("a", calls=80, grow=500, gap=20 * 60)
    advice = advise_cache(away, current="5m", weigh=_weigh)
    assert advice is not None and advice.best == "1h" and advice.one_hour < advice.five_minutes

    quick = _conversation("b", calls=80, grow=500, gap=20)
    advice = advise_cache(quick, current="5m", weigh=_weigh)
    assert advice is not None and advice.best == "5m"


def test_the_cache_lifetime_counts_only_calls_it_applies_to():
    other = _conversation("a", calls=80, gap=20 * 60, api="openai-responses")
    assert advise_cache(other, current="5m", weigh=_weigh) is None


def test_tune_reads_the_ledger_and_the_settings(monkeypatch, tmp_path):
    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "usage.db")
    events = [
        UsageEvent(ts=1_000 + c.ts, session_id=c.stream, call_kind="chat",
                   provider="anthropic", api=c.api, model_id="claude-x",
                   input_tokens=c.prompt, output_tokens=c.out)
        for c in _conversation("s1", calls=120, compact_at=180_000)
    ]
    events.append(UsageEvent(ts=1_000, session_id="s1", call_kind="compaction",
                             provider="anthropic", model_id="claude-x", input_tokens=99))
    ledger.append_many(events)
    monkeypatch.setattr(settings.user_config, "read_config",
                        lambda: {"context": {"auto_compact_percent": 95}})

    result = tuning.tune(days=1, ledger=ledger, now=2_000 + 120 * 30,
                         window_of=lambda model: WINDOW, weigh=_weigh)
    ledger.close()

    assert [a.model for a in result.compaction] == ["anthropic/claude-x"]
    assert result.compaction[0].current == 95
    assert result.compaction[0].facts.calls == 120  # the compaction call is not a turn
    assert result.cache is not None and result.cache.current == "5m"
    assert result.to_dict()["compaction"][0]["model"] == "anthropic/claude-x"


@pytest.mark.parametrize(("stored", "expected"), [
    ({}, 80), ({"context": {"auto_compact_percent": 60}}, 60),
    ({"context": {"auto_compact_percent": 20}}, 80), ({"context": {"auto_compact_percent": "x"}}, 80),
])
def test_the_compaction_share_is_read_live_from_settings(monkeypatch, stored, expected):
    monkeypatch.setattr(settings.user_config, "read_config", lambda: stored)
    assert DefaultContextEngine().auto_compact_pct == expected / 100
    assert DefaultContextEngine(auto_compact_pct=0.5).auto_compact_pct == 0.5


@pytest.mark.parametrize(("setting", "asked", "expected"), [
    ("5m", None, "short"), ("1h", None, "long"), ("1h", "short", "long"),
    ("1h", "none", "none"), ("5m", "long", "long"),
])
def test_the_cache_lifetime_setting_lengthens_only_a_plain_cache(monkeypatch, setting, asked, expected):
    monkeypatch.setattr(settings.user_config, "read_config",
                        lambda: {"models": {"prompt_cache_ttl": setting}})
    assert with_lifetime_setting(asked) == expected
