"""Where a request's cached prefix broke, recorded on its usage event.

Each request is compared, before it is sent, with the previous request on
its lane; the reply's cached input then tells a lapsed cache lifetime from
a provider-side miss (``models/usage/prefix_fingerprint.py``).
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.recording import capture
from openprogram.models.providers.recording.capture import RESPONSES, CapturedRequest
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    Model,
    SimpleStreamOptions,
    Usage,
)
from openprogram.models.usage import context as usage_context
from openprogram.models.usage import prefix_fingerprint as prefix
from openprogram.models.usage import recorder
from openprogram.models.usage.context import UsageContext
from openprogram.models.usage.ledger import UsageLedger

MAIN = UsageContext(session_id="s1", call_kind="chat", agent_id="main")
VERIFY = UsageContext(session_id="s1", call_kind="chat", agent_id="main", prefix_lane="goal_verification")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    lg = UsageLedger(db_path=tmp_path / "usage.db")
    monkeypatch.setattr("openprogram.models.usage.ledger.default_ledger", lg)
    monkeypatch.setattr(recorder, "default_ledger", lg)
    return lg


@pytest.fixture(autouse=True)
def _sections(monkeypatch):
    """System prompts here are joined from ``name=text`` lines."""
    def parts(text):
        lines = text.splitlines(keepends=True)
        return [(line.split("=", 1)[0], line) for line in lines]

    monkeypatch.setattr(capture, "_system_sections", parts)


def _request(*history, instructions="base=you help\nopen_work=nothing open\n", effort="low", model="m1"):
    body = {"model": model, "instructions": instructions, "reasoning": {"effort": effort},
            "prompt_cache_key": "s1",
            "tools": [{"type": "function", "name": "read", "parameters": {}}],
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}
                      for text in history]}
    return CapturedRequest(wire=RESPONSES, provider="openai", api="openai-responses", model_id=model, body=body)


def _check(request, ledger, context=MAIN, now=1000.0):
    """Check ``request`` and settle it, as an answered request is."""
    check = prefix.check_request(request, context=context, ledger=ledger, now=now)
    return prefix.settle_check(check, None, ledger=ledger)


def test_a_request_that_only_appends_keeps_its_prefix(ledger):
    assert _check(_request("hi"), ledger).break_cause is None
    check = _check(_request("hi", "and more"), ledger, now=1010.0)
    assert (check.break_at, check.break_cause) == (None, None)


def test_a_changed_section_of_the_instructions_is_named(ledger):
    _check(_request("hi"), ledger)
    check = _check(_request("hi", "next", instructions="base=you help\nopen_work=one promise\n"),
                   ledger, now=1010.0)
    assert check.break_cause == "system:open_work"
    assert check.break_at == 1  # the system text, after the tool


def test_a_changed_history_item_and_a_shorter_request_are_breaks(ledger):
    _check(_request("hi", "there"), ledger)
    assert _check(_request("hi", "THERE", "x"), ledger, now=1001.0).break_cause == "history:user"
    assert _check(_request("hi"), ledger, now=1002.0).break_cause == "removed:history:user"


@pytest.mark.parametrize("change,cause", [
    ({"model": "m2"}, "model"),
    ({"effort": "high"}, "setting:effort"),
])
def test_a_changed_setting_is_its_own_cause(ledger, change, cause):
    _check(_request("hi"), ledger)
    assert _check(_request("hi", "next", **change), ledger, now=1001.0).break_cause == cause


def test_a_verification_turn_has_its_own_lane(ledger):
    """A Goal verification turn has other instructions; alternating with the
    conversation's own turns, neither lane reports a break."""
    verifying = "base=you help\nverify=check the candidate\n"
    checks = [
        _check(_request("hi"), ledger, MAIN, 1000.0),
        _check(_request("hi", "verify", instructions=verifying), ledger, VERIFY, 1001.0),
        _check(_request("hi", "more"), ledger, MAIN, 1002.0),
        _check(_request("hi", "verify", "again", instructions=verifying), ledger, VERIFY, 1003.0),
        _check(_request("hi", "more", "still"), ledger, MAIN, 1004.0),
    ]
    assert [check.break_cause for check in checks] == [None] * 5


def test_the_baseline_outlives_a_restart(ledger, tmp_path):
    _check(_request("hi"), ledger)
    restarted = UsageLedger(db_path=tmp_path / "usage.db")
    check = prefix.check_request(_request("hi", "next", instructions="base=you help\nopen_work=one\n"),
                                 context=MAIN, ledger=restarted, now=1001.0)
    assert check.break_cause == "system:open_work"


def test_an_announced_break_is_recorded_as_expected(ledger):
    _check(_request("hi", "a long tool result"), ledger)
    prefix.expect_break("s1", "aging", ledger=ledger)
    prefix.expect_break("s1", "aging", ledger=ledger)
    prefix.expect_break("s1", "compaction", ledger=ledger)
    check = _check(_request("hi", "[aged]", "next"), ledger, now=1001.0)
    assert (check.break_cause, check.expected) == ("history:user", "aging,compaction")
    # Used once: the next break is not explained by it.
    assert _check(_request("hi", "changed"), ledger, now=1002.0).expected is None


def _unsettled(request, ledger, context=MAIN, now=1000.0):
    return prefix.check_request(request, context=context, ledger=ledger, now=now)


@pytest.mark.parametrize("gap,cause", [(10.0, "server"), (600.0, "ttl")])
def test_a_drop_in_cached_input_is_put_down_to_the_lifetime_or_the_provider(ledger, gap, cause):
    first = _unsettled(_request("hi"), ledger)
    prefix.settle_check(first, Usage(input=100, cache_read=50_000), ledger=ledger)
    second = _unsettled(_request("hi", "next"), ledger, now=1000.0 + gap)
    settled = prefix.settle_check(second, Usage(input=50_000, cache_read=0), ledger=ledger)
    assert settled.break_cause == cause and settled.break_at is None


def test_a_small_drop_in_cached_input_is_not_a_break(ledger):
    first = _unsettled(_request("hi"), ledger)
    prefix.settle_check(first, Usage(input=100, cache_read=50_000), ledger=ledger)
    second = _unsettled(_request("hi", "next"), ledger, now=1010.0)
    assert prefix.settle_check(second, Usage(input=1_500, cache_read=48_500), ledger=ledger).break_cause is None


def test_the_same_bytes_cut_differently_are_not_a_break(ledger, tmp_path, monkeypatch):
    """After a restart the runtime no longer knows how a prompt it sends
    again was composed; its bytes still match the baseline."""
    _check(_request("hi"), ledger)
    monkeypatch.setattr(capture, "_system_sections", None)
    restarted = UsageLedger(db_path=tmp_path / "usage.db")
    check = prefix.settle_check(prefix.check_request(_request("hi", "next"), context=MAIN,
                                                     ledger=restarted, now=1001.0), None, ledger=restarted)
    assert (check.break_at, check.break_cause) == (None, None)
    # A changed prompt is still named, at the first section end both cuts share.
    changed = prefix.check_request(_request("hi", "next", "x", instructions="base=you help\nopen_work=one\n"),
                                   context=MAIN, ledger=restarted, now=1002.0)
    assert changed.break_cause == "system:prompt"


def test_a_check_that_was_not_answered_leaves_the_baseline(ledger):
    """A request that got no reply is not what the next one is compared with."""
    _check(_request("hi"), ledger)
    _unsettled(_request("hi", "refused shape"), ledger, now=1001.0)
    assert _check(_request("hi", "next"), ledger, now=1002.0).break_cause is None


def test_a_side_call_between_turns_is_not_compared(ledger):
    """A permission check, a title or memory writing sends a prompt of its
    own: it has no lane, and the turns around it see no break."""
    side = [UsageContext(session_id="s1", call_kind=kind, agent_id="main")
            for kind in ("permission", "title", "memory", "summarize", "prediction", "tool")]
    checks = [_check(_request("hi"), ledger)]
    for n, context in enumerate(side, 1):
        checks.append(_check(_request(f"judge {n}", instructions="classify\n"), ledger, context, 1000.0 + n))
    checks.append(_check(_request("hi", "more"), ledger, now=1010.0))
    assert [check.lane is None for check in checks] == [False, *[True] * len(side), False]
    assert [check.break_cause for check in checks] == [None] * len(checks)


def test_a_branch_is_compared_with_its_own_requests(ledger):
    """Editing an earlier message starts a branch: its first request breaks
    where it left the old branch, as expected; back on the old branch, a
    request extends that branch's own last request."""
    a = UsageContext(session_id="s1", call_kind="chat", agent_id="main", branch_id="A")
    b = UsageContext(session_id="s1", call_kind="chat", agent_id="main", branch_id="B")
    _check(_request("one", "two", "three"), ledger, a)
    edited = _check(_request("one", "TWO"), ledger, b, 1001.0)
    assert (edited.break_at, edited.break_cause, edited.expected) == (3, "history:user", "branch")
    assert _check(_request("one", "TWO", "more"), ledger, b, 1002.0).break_cause is None
    assert _check(_request("one", "two", "three", "back"), ledger, a, 1003.0).break_cause is None


def test_a_break_announced_for_the_call_being_prepared_stays_on_its_lane(ledger):
    """Aging and compaction rewrite the request about to go out: they tell
    that lane only, not the session's other lanes."""
    _check(_request("hi"), ledger, MAIN)
    _check(_request("hi"), ledger, VERIFY, 1001.0)
    token = usage_context._current.set(MAIN)
    try:
        prefix.expect_break(None, "aging", ledger=ledger)
    finally:
        usage_context._current.reset(token)
    assert _check(_request("[aged]"), ledger, MAIN, 1002.0).expected == "aging"
    assert _check(_request("changed"), ledger, VERIFY, 1003.0).expected is None


def test_a_mended_resend_replaces_the_refused_request():
    """A request sent again inside one attempt, mended after the provider
    refused its shape, is the one the attempt's slot holds and checks."""
    import contextvars

    seen = []
    slot = capture.RequestSlot(on_capture=lambda request: seen.append(request.body["n"]) or request.body["n"])
    model = Model(id="m1", name="m1", api="x", provider="p", base_url="http://x")

    def attempt():
        capture.bind_slot(slot)
        capture.capture_request(model, {"n": 1}, wire=RESPONSES)
        capture.reopen_slot()
        capture.capture_request(model, {"n": 2}, wire=RESPONSES)
        capture.capture_request(model, {"n": 3}, wire=RESPONSES)  # another adapter call: left alone

    contextvars.copy_context().run(attempt)
    assert seen == [1, 2] and slot.request.body == {"n": 2} and slot.result == 2


def test_a_call_outside_a_session_is_not_compared(ledger):
    check = _check(_request("hi"), ledger, UsageContext(call_kind="title"))
    assert check.lane is None and check.prefix_hash


class _Adapter:
    """A provider that hands its body to the capture point, as adapters do."""

    def __init__(self, bodies):
        self.bodies = bodies

    async def stream_simple(self, model, context, opts):
        capture.capture_request(model, self.bodies.pop(0), wire=RESPONSES)
        message = AssistantMessage(content=[], api=model.api, provider=model.provider, model=model.id,
                                   timestamp=0, usage=Usage(input=100, output=5, cache_read=0))
        yield EventStart(partial=message)
        yield EventDone(reason="stop", message=message)


def test_the_usage_event_records_the_check(ledger, monkeypatch):
    from openprogram.models.providers.streaming import stream as stream_module

    model = Model(id="m1", name="m1", api="fake-capture", provider="fakeprov", base_url="http://x")
    bodies = [_request("hi").body,
              _request("hi", "next", instructions="base=you help\nopen_work=one promise\n").body]
    monkeypatch.setattr(stream_module, "get_api_provider", lambda api: _Adapter(bodies))
    monkeypatch.setattr(stream_module, "resolve_provider_key", lambda provider: "k")

    async def one():
        async for _event in stream_module.stream_simple(model, Context(), SimpleStreamOptions(session_id="s1")):
            pass

    token = usage_context._current.set(MAIN)
    try:
        asyncio.run(one())
        asyncio.run(one())
    finally:
        usage_context._current.reset(token)
    with ledger.read() as conn:
        rows = conn.execute("SELECT prefix_hash, break_at, break_cause FROM usage_events ORDER BY ts").fetchall()
    assert [row["break_cause"] for row in rows] == [None, "system:open_work"]
    assert all(row["prefix_hash"] for row in rows)


def test_a_setting_for_every_conversation_is_expected_on_every_lane(ledger):
    """The cache lifetime applies to all conversations: changing it tells
    every lane of every session that its next request breaks on purpose."""
    other = UsageContext(session_id="s2", call_kind="chat", agent_id="main")
    _check(_request("hi"), ledger)
    _check(_request("hi"), ledger, context=other)
    prefix.expect_break_everywhere("ttl_switch", ledger=ledger)
    assert _check(_request("hi", "next", effort="high"), ledger, now=1001.0).expected == "ttl_switch"
    assert _check(_request("hi", "next", effort="high"), ledger, context=other, now=1001.0).expected == "ttl_switch"


def test_changing_the_cache_lifetime_setting_announces_the_break(monkeypatch):
    from openprogram.state import settings

    seen = []
    monkeypatch.setattr(prefix, "expect_break_everywhere", lambda cause, **_k: seen.append(cause))
    monkeypatch.setattr(settings.user_config, "update_config", lambda _fn: None)
    assert settings.set_setting("models.prompt_cache_ttl", "1h").get("value") == "1h"
    assert seen == ["ttl_switch"]
