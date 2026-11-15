"""Every streamed reply carries how long it took to begin, into the usage ledger."""
from __future__ import annotations

from openprogram.models.providers.streaming.first_token import FirstToken
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    EventTextDelta,
    EventThinkingDelta,
    Model,
    TextContent,
    Usage,
)
from openprogram.models.usage.recorder import build_message_event


def _reply(text: str = "") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)] if text else [], api="completion",
                            provider="fake", model="m", timestamp=1,
                            usage=Usage(input=10, output=5, tokens_reported=True))


def test_first_content_and_first_text_are_told_apart(monkeypatch):
    from openprogram.models.providers.streaming import first_token

    clock = [100.0]
    monkeypatch.setattr(first_token.time, "monotonic", lambda: clock[0])
    watch = FirstToken()
    watch.see(EventStart(partial=_reply()))
    assert watch.first_token_ms is None
    clock[0] += 0.25
    watch.see(EventThinkingDelta(content_index=0, delta="hm", partial=_reply()))
    clock[0] += 1.5
    watch.see(EventTextDelta(content_index=1, delta="Hi", partial=_reply("Hi")))
    assert (watch.first_token_ms, watch.first_text_ms) == (250, 1750)

    final = _reply("Hi")
    watch.stamp(EventDone(reason="stop", message=final))
    assert (final.usage.first_token_ms, final.usage.first_text_ms) == (250, 1750)


def test_a_dict_shaped_terminal_event_is_stamped_too():
    watch = FirstToken()
    watch.see({"type": "text_delta", "delta": "x"})
    event = {"type": "done", "message": {"usage": {}}}
    watch.stamp(event)
    assert event["message"]["usage"]["first_text_ms"] is not None


def test_a_reply_with_no_content_carries_no_timing():
    watch = FirstToken()
    final = _reply()
    watch.stamp(EventDone(reason="stop", message=final))
    assert final.usage.first_token_ms is None


def test_the_usage_event_and_ledger_keep_it(tmp_path):
    from openprogram.models.usage.ledger import UsageLedger

    final = _reply("Hi")
    final.usage.first_token_ms, final.usage.first_text_ms = 120, 900
    model = Model(id="m", name="m", api="completion", provider="fake", base_url="https://x.invalid")
    event = build_message_event(model, final)
    assert (event.first_token_ms, event.first_text_ms) == (120, 900)

    ledger = UsageLedger(tmp_path / "usage.db")
    ledger.append(event)
    import sqlite3

    row = sqlite3.connect(tmp_path / "usage.db").execute(
        "select first_token_ms, first_text_ms from usage_events").fetchone()
    assert row == (120, 900)
