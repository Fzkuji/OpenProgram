"""Typed event layer: Event / make_event / EventBus.subscribe / singleton.

Legacy channel API (emit("channel", data) / on()) is covered too — it must
keep working untouched while the typed layer rides alongside.
"""
import threading

import pytest

from openprogram.agent.event_bus import (
    Event,
    EventBus,
    create_event_bus,
    get_event_bus,
    make_event,
)


# ─── Event / make_event ──────────────────────────────────────────────────────

def test_make_event_fills_identity_and_defaults():
    ev = make_event("tool.before", "agent", payload={"tool": "bash"})
    assert ev.type == "tool.before"
    assert ev.origin == "agent"
    assert ev.payload == {"tool": "bash"}
    assert ev.id and ev.ts > 0
    assert isinstance(ev.metadata, dict)


def test_make_event_explicit_metadata_wins_over_auto():
    ev = make_event("x", "system", metadata={"session": "explicit"})
    assert ev.metadata["session"] == "explicit"


def test_event_is_frozen():
    ev = make_event("x", "system")
    with pytest.raises(Exception):
        ev.type = "y"  # type: ignore[misc]


def test_make_event_picks_up_turn_contextvar():
    from openprogram.store import _current_turn_id

    token = _current_turn_id.set("turn_abc")
    try:
        ev = make_event("tool.after", "tool")
        assert ev.metadata.get("turn") == "turn_abc"
    finally:
        _current_turn_id.reset(token)


def test_make_event_outside_turn_has_no_turn_key():
    ev = make_event("credential.cooldown", "system")
    assert "turn" not in ev.metadata


# ─── typed subscribe / emit ──────────────────────────────────────────────────

def test_subscribe_receives_emitted_event():
    bus = create_event_bus()
    got = []
    bus.subscribe(got.append)
    ev = make_event("a.b", "user")
    bus.emit(ev)
    assert got == [ev]


def test_subscribe_with_types_filters():
    bus = create_event_bus()
    got = []
    bus.subscribe(got.append, types={"tool.before"})
    bus.emit(make_event("tool.after", "tool"))
    bus.emit(make_event("tool.before", "agent"))
    assert [e.type for e in got] == ["tool.before"]


def test_unsubscribe_stops_delivery():
    bus = create_event_bus()
    got = []
    unsub = bus.subscribe(got.append)
    unsub()
    bus.emit(make_event("a", "user"))
    assert got == []


def test_raising_handler_does_not_break_emit_or_other_handlers():
    bus = create_event_bus()
    got = []

    def bad(_ev: Event) -> None:
        raise RuntimeError("boom")

    bus.subscribe(bad)
    bus.subscribe(got.append)
    bus.emit(make_event("a", "user"))  # must not raise
    assert len(got) == 1


# ─── legacy channel API stays intact ─────────────────────────────────────────

def test_legacy_channel_emit_still_works():
    bus = create_event_bus()
    got = []
    bus.on("agent", got.append)
    bus.emit("agent", {"k": 1})
    assert got == [{"k": 1}]


def test_typed_and_legacy_are_isolated():
    bus = create_event_bus()
    typed, legacy = [], []
    bus.subscribe(typed.append)
    bus.on("agent", legacy.append)
    bus.emit("agent", {"k": 1})
    bus.emit(make_event("a", "user"))
    assert len(typed) == 1 and len(legacy) == 1


# ─── singleton ───────────────────────────────────────────────────────────────

def test_get_event_bus_is_one_instance_across_threads():
    seen = []

    def grab() -> None:
        seen.append(get_event_bus())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(b) for b in seen + [get_event_bus()]}) == 1
