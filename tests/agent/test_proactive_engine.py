"""Proactive 引擎核心逻辑：gate 仲裁 / observer 分发 / cooldown / 防自触发 / fail-safe。

用假 policy 钉死引擎，不依赖真规则、真 LLM。
"""
from __future__ import annotations

import pytest

from openprogram.agent.event_bus import Event, make_event
from openprogram.proactive import (
    Gate, Notify, Policy, clear_policies, register_policy,
)
from openprogram.proactive import engine as E
from openprogram.proactive.state import get_state_store


@pytest.fixture(autouse=True)
def _clean():
    clear_policies()
    E._last_fired.clear()
    yield
    clear_policies()
    E._last_fired.clear()


def _tool_before(tool="bash", cmd="rm -rf /x", session="s1", ts=1000.0):
    ev = make_event("tool.before", "agent",
                    {"tool": tool, "args": {"command": cmd}}, {"session": session})
    return Event(id=ev.id, ts=ts, type=ev.type, origin=ev.origin,
                 payload=ev.payload, metadata=ev.metadata)


# ─── gate 仲裁 ───────────────────────────────────────────────────────────────

def test_gate_no_policies_allows():
    assert E._gate_fn(_tool_before()) is None


def test_gate_deny_returns_reason():
    class P(Policy):
        on = {"tool.before"}; lane = "gate"
        def evaluate(self, e, s): return Gate.deny("危险")
    register_policy(P())
    assert E._gate_fn(_tool_before()) == "危险"


def test_gate_strictest_wins_deny_over_ask():
    class Asker(Policy):
        on = {"tool.before"}; lane = "gate"; name = "Asker"
        def evaluate(self, e, s): return Gate.ask("确认?")
    class Denier(Policy):
        on = {"tool.before"}; lane = "gate"; name = "Denier"
        def evaluate(self, e, s): return Gate.deny("拦死")
    register_policy(Asker()); register_policy(Denier())
    assert E._gate_fn(_tool_before()) == "拦死"   # deny 压过 ask


def test_gate_ask_when_no_deny():
    class Asker(Policy):
        on = {"tool.before"}; lane = "gate"
        def evaluate(self, e, s): return Gate.ask("确认?")
    register_policy(Asker())
    out = E._gate_fn(_tool_before())
    assert out is not None and "确认?" in out


def test_gate_allow_is_noop():
    class P(Policy):
        on = {"tool.before"}; lane = "gate"
        def evaluate(self, e, s): return Gate.allow()
    register_policy(P())
    assert E._gate_fn(_tool_before()) is None


def test_gate_policy_error_fails_open():
    class Bad(Policy):
        on = {"tool.before"}; lane = "gate"
        def evaluate(self, e, s): raise RuntimeError("boom")
    register_policy(Bad())
    assert E._gate_fn(_tool_before()) is None   # 不抛、按放行


# ─── observer 分发 + 落地 ────────────────────────────────────────────────────

def test_observer_fires_and_lands_notify(monkeypatch):
    landed = []
    monkeypatch.setattr(E, "emit_ws_frame", lambda frame: landed.append(frame))

    class Watcher(Policy):
        on = {"tool.after"}; lane = "observer"
        def evaluate(self, e, s): return Notify("卡住了", severity="warn")
    register_policy(Watcher())

    ev = make_event("tool.after", "tool", {"tool": "bash", "is_error": True},
                    {"session": "s1"})
    E._observer_consumer(ev)
    assert len(landed) == 1
    assert landed[0]["type"] == "event"
    assert landed[0]["data"]["kind"] == "proactive.notify"
    assert landed[0]["data"]["message"] == "卡住了"


def test_observer_filters_by_on(monkeypatch):
    fired = []
    class Watcher(Policy):
        on = {"tool.after"}; lane = "observer"
        def evaluate(self, e, s): fired.append(1); return None
    register_policy(Watcher())
    # 喂一个它不订阅的事件类型
    E._observer_consumer(make_event("model.response_started", "agent", {}, {"session": "s1"}))
    assert fired == []


def test_observer_skips_proactive_origin():
    fired = []
    class Watcher(Policy):
        on = {"tool.after"}; lane = "observer"
        def evaluate(self, e, s): fired.append(1); return None
    register_policy(Watcher())
    ev = Event(id="x", ts=1.0, type="tool.after", origin="proactive",
               payload={"tool": "bash"}, metadata={"session": "s1"})
    E._observer_consumer(ev)
    assert fired == []   # 防自触发


def test_observer_cooldown(monkeypatch):
    landed = []
    monkeypatch.setattr(E, "emit_ws_frame", lambda frame: landed.append(frame))
    class Watcher(Policy):
        on = {"tool.after"}; lane = "observer"; cooldown_s = 100.0
        def evaluate(self, e, s): return Notify("x")
    register_policy(Watcher())

    def at(ts):
        ev = make_event("tool.after", "tool", {"tool": "bash"}, {"session": "s1"})
        return Event(id=ev.id, ts=ts, type=ev.type, origin=ev.origin,
                     payload=ev.payload, metadata=ev.metadata)
    E._observer_consumer(at(1000.0))   # 出手
    E._observer_consumer(at(1050.0))   # 冷却中，压制
    E._observer_consumer(at(1200.0))   # 冷却过，再出手
    assert len(landed) == 2


def test_observer_policy_error_isolated(monkeypatch):
    landed = []
    monkeypatch.setattr(E, "emit_ws_frame", lambda frame: landed.append(frame))
    class Bad(Policy):
        on = {"tool.after"}; lane = "observer"; name = "Bad"
        def evaluate(self, e, s): raise RuntimeError("boom")
    class Good(Policy):
        on = {"tool.after"}; lane = "observer"; name = "Good"
        def evaluate(self, e, s): return Notify("ok")
    register_policy(Bad()); register_policy(Good())
    E._observer_consumer(make_event("tool.after", "tool", {"tool": "bash"}, {"session": "s1"}))
    assert len(landed) == 1   # Bad 出错不影响 Good


# ─── state fold（observer 读累积状况）─────────────────────────────────────────

def test_state_fold_counts_tool_failures():
    store = get_state_store()
    sid = "sfold"
    for _ in range(3):
        store.apply(make_event("tool.after", "tool",
                               {"tool": "bash", "is_error": True}, {"session": sid}))
    assert store.get(sid).tool_fail_count["bash"] == 3
    store.apply(make_event("tool.after", "tool",
                           {"tool": "bash", "is_error": False}, {"session": sid}))
    assert store.get(sid).tool_fail_count["bash"] == 0   # 成功清零


def test_state_turn_reset_and_verification():
    store = get_state_store()
    sid = "sturn"
    store.apply(make_event("user.prompt_submitted", "user", {}, {"session": sid}))
    store.apply(make_event("file.changed", "tool", {"path": "a.py"}, {"session": sid}))
    st = store.get(sid)
    assert st.turn_has_file_change and not st.turn_has_verification
    store.apply(make_event("tool.before", "agent",
                           {"tool": "bash", "args": {"command": "pytest"}}, {"session": sid}))
    assert store.get(sid).turn_has_verification
    # 新一轮重置
    store.apply(make_event("user.prompt_submitted", "user", {}, {"session": sid}))
    assert not store.get(sid).turn_has_file_change
