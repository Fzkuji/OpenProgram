"""Plugin hooks as bus subscribers（plugins/hooks.py）。

注册即订阅、退订即失效、gate handler 参与否决、handler 异常不外抛。
"""
from __future__ import annotations

import pytest

from openprogram.events import (
    ToolGateDenied,
    create_event_bus,
    decide_tool_gate,
    make_event,
)
from openprogram.plugins import hooks


@pytest.fixture
def bus(monkeypatch):
    b = create_event_bus()
    import openprogram.events.bus as EB
    monkeypatch.setattr(EB, "_event_bus", b)
    yield b
    hooks.unregister_plugin_hooks("p1")
    hooks.unregister_plugin_hooks("p2")


def test_register_subscribes_notify_handler(bus):
    got = []
    hooks.register_plugin_hooks("p1", {"tool.after": got.append})
    ev = make_event("tool.after", "tool", {"tool": "bash", "is_error": False})
    bus.emit(ev)
    assert got == [ev]


def test_unregister_disposes_subscriptions(bus):
    got = []
    hooks.register_plugin_hooks("p1", {"tool.after": got.append})
    hooks.unregister_plugin_hooks("p1")
    bus.emit(make_event("tool.after", "tool"))
    assert got == []


def test_reregister_replaces_previous_mapping(bus):
    first, second = [], []
    hooks.register_plugin_hooks("p1", {"tool.after": first.append})
    hooks.register_plugin_hooks("p1", {"tool.after": second.append})
    bus.emit(make_event("tool.after", "tool"))
    assert first == [] and len(second) == 1


def test_gate_handler_denies_with_reason_string(bus):
    hooks.register_plugin_hooks(
        "p1", {"tool.before": lambda ev: f"no {ev.payload['tool']}"})
    ev = make_event("tool.before", "agent", {"tool": "bash", "args": {}})
    assert decide_tool_gate(ev) == "no bash"


def test_gate_handler_denies_via_tool_gate_denied(bus):
    def deny(ev):
        raise ToolGateDenied("blocked by policy")
    hooks.register_plugin_hooks("p1", {"tool.before": deny})
    ev = make_event("tool.before", "agent", {"tool": "bash", "args": {}})
    assert decide_tool_gate(ev) == "blocked by policy"


def test_gate_handler_falsy_allows(bus):
    hooks.register_plugin_hooks("p1", {"tool.before": lambda ev: None})
    hooks.register_plugin_hooks("p2", {"tool.before": lambda ev: ""})
    ev = make_event("tool.before", "agent", {"tool": "bash", "args": {}})
    assert decide_tool_gate(ev) is None


def test_gate_handler_exception_is_fail_open(bus, caplog):
    def boom(ev):
        raise RuntimeError("bug")
    hooks.register_plugin_hooks("p1", {"tool.before": boom})
    ev = make_event("tool.before", "agent", {"tool": "bash", "args": {}})
    with caplog.at_level("WARNING", logger="openprogram.plugins.hooks"):
        assert decide_tool_gate(ev) is None
    assert any("fail-open" in r.getMessage() for r in caplog.records)


def test_notify_handler_exception_logged_not_raised(bus, caplog):
    def boom(ev):
        raise RuntimeError("bug")
    hooks.register_plugin_hooks("p1", {"session.start": boom})
    with caplog.at_level("WARNING", logger="openprogram.plugins.hooks"):
        bus.emit(make_event("session.start", "system", {"session_id": "s"}))
    assert any("p1@session.start" in r.getMessage() for r in caplog.records)


def test_non_callable_and_non_string_entries_skipped(bus):
    got = []
    hooks.register_plugin_hooks("p1", {
        "tool.after": got.append,
        "chat.before_send": "not-callable",
        42: got.append,
    })
    bus.emit(make_event("tool.after", "tool"))
    bus.emit(make_event("chat.before_send", "user"))
    assert len(got) == 1
