"""Gate dispatch + registry + shell subscribers + event log + turn.stop.

Covers: EVENTS registry shape, emit_gate merge/order/fail-open/re-entrancy,
shell subscriber exit-code protocol (0/2/other) and timeout, event-log
routing and rotation, and the dispatcher's hook-continuation helper
(``continue_stop_hook_turns``) including its stop_hook_active flag
protocol (no numeric cap).
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openprogram.events import (
    EVENTS,
    EventSpec,
    GateOutcome,
    create_event_bus,
    install_config_hooks,
    make_event,
    make_shell_gate,
    make_shell_notifier,
)
from openprogram.events import bus as eb


def _python_command(source: str, *args: object) -> str:
    argv = [sys.executable, "-c", source, *(str(arg) for arg in args)]
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _ev(type: str = "tool.before", **payload):
    return make_event(type, "agent", dict(payload))


# 注册表

def test_registry_lists_the_wired_events_with_kinds():
    kinds = {name: spec.kind for name, spec in EVENTS.items()}
    assert kinds["tool.before"] == "gate"
    assert kinds["turn.stop"] == "gate"
    notify_only = set(kinds) - {"tool.before", "turn.stop"}
    assert all(kinds[n] == "notify" for n in notify_only)
    for spec in EVENTS.values():
        assert isinstance(spec, EventSpec) and spec.payload_doc


def test_every_emitted_type_is_registered():
    """Every emit_safe type string in the codebase must be in EVENTS —
    the admission boundary holds with zero migration warnings."""
    import re
    root = Path(__file__).resolve().parents[3] / "openprogram"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    )
    emitted = set(re.findall(r'emit_safe\(\s*"([a-z_.]+)"', source))
    assert emitted <= set(EVENTS), f"unregistered: {emitted - set(EVENTS)}"


def test_emit_of_unregistered_type_warns_once(caplog):
    bus = create_event_bus()
    eb._warned_unregistered.discard("not.registered")
    with caplog.at_level("WARNING", logger="openprogram.events.bus"):
        bus.emit(make_event("not.registered", "system"))
        bus.emit(make_event("not.registered", "system"))
    hits = [r for r in caplog.records if "not.registered" in r.getMessage()]
    assert len(hits) == 1


# emit_gate 语义

def test_emit_gate_no_gates_allows():
    out = create_event_bus().emit_gate(_ev())
    assert out == GateOutcome(allowed=True, reasons=[])


def test_emit_gate_merges_reasons_in_registration_order():
    bus = create_event_bus()
    bus.subscribe_gate("tool.before", lambda ev: "一")
    bus.subscribe_gate("tool.before", lambda ev: None)
    bus.subscribe_gate("tool.before", lambda ev: "二")
    out = bus.emit_gate(_ev())
    assert out.allowed is False
    assert out.reasons == ["一", "二"]


def test_emit_gate_raising_gate_fails_open():
    bus = create_event_bus()

    def bad(ev):
        raise RuntimeError("gate bug")

    bus.subscribe_gate("tool.before", bad)
    assert bus.emit_gate(_ev()).allowed is True


def test_emit_gate_unregister():
    bus = create_event_bus()
    unreg = bus.subscribe_gate("tool.before", lambda ev: "拦")
    assert bus.emit_gate(_ev()).allowed is False
    unreg()
    assert bus.emit_gate(_ev()).allowed is True


def test_emit_gate_reentrancy_allows_inner_call():
    bus = create_event_bus()
    inner: list[GateOutcome] = []

    def reentrant(ev):
        inner.append(bus.emit_gate(_ev()))   # same type, same thread
        return "外层拦"

    bus.subscribe_gate("tool.before", reentrant)
    out = bus.emit_gate(_ev())
    assert inner == [GateOutcome(allowed=True, reasons=[])]  # 直接放行
    assert out.reasons == ["外层拦"]        # 外层正常裁决


def test_emit_gate_timeout_budget_skips_remaining_gates():
    bus = create_event_bus()
    asked: list[str] = []

    def slow(ev):
        import time
        asked.append("slow")
        time.sleep(0.05)
        return None

    def never(ev):
        asked.append("never")
        return "不该被问到"

    bus.subscribe_gate("tool.before", slow)
    bus.subscribe_gate("tool.before", never)
    out = bus.emit_gate(_ev(), timeout_s=0.01)
    assert asked == ["slow"] and out.allowed is True


# shell 订阅者：退出码协议 + 超时

def test_shell_gate_exit_zero_allows():
    assert make_shell_gate(_python_command("raise SystemExit(0)"))(_ev()) is None


def test_shell_gate_exit_two_denies_with_stderr_reason():
    reason = make_shell_gate(_python_command(
        "import sys; sys.stderr.write('还没写测试'); raise SystemExit(2)",
    ))(_ev())
    assert reason == "还没写测试"


def test_shell_gate_other_exit_code_fails_open():
    command = _python_command("raise SystemExit(3)")
    assert make_shell_gate(command)(_ev()) is None


def test_shell_gate_timeout_fails_open():
    command = _python_command("import time; time.sleep(5)")
    assert make_shell_gate(command, timeout_s=0.2)(_ev()) is None


def test_shell_gate_receives_event_json_on_stdin(tmp_path):
    out = tmp_path / "stdin.json"
    command = _python_command(
        "import pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')",
        out,
    )
    gate = make_shell_gate(command)
    ev = _ev(tool="bash")
    assert gate(ev) is None
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["type"] == "tool.before" and data["payload"]["tool"] == "bash"


def test_shell_notifier_runs_in_background_and_ignores_exit_code(tmp_path):
    out = tmp_path / "ran"
    command = _python_command(
        "import pathlib, sys; pathlib.Path(sys.argv[1]).touch(); raise SystemExit(7)",
        out,
    )
    notify = make_shell_notifier(command)
    notify(_ev("turn.start"))
    for _ in range(100):
        if out.exists():
            break
        threading.Event().wait(0.02)
    assert out.exists()


def test_install_config_hooks_registers_by_kind():
    bus = create_event_bus()
    n = install_config_hooks(bus=bus, hooks_config={
        "tool.before": [{"command": "exit 0"}],
        "turn.stop": [{"command": "exit 2", "timeout": 5}],
        "turn.start": [{"command": "true"}],
        "unknown.event": [{"command": "true"}],
        "turn.end": [{}],                       # no command → skipped
    })
    assert n == 3
    with bus._lock:
        assert sorted(bus._gates) == ["tool.before", "turn.stop"]
        assert len(bus._subscribers) == 1


# 事件日志：路由 + gate 附加字段 + 轮转

@pytest.fixture()
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_event_log_falls_back_when_session_dir_missing(_home):
    bus = create_event_bus()
    bus.log_events = True
    bus.emit(make_event("turn.start", "system", {},
                        {"session": "no-such-session"}))
    fallback = _home / ".openprogram" / "logs" / "events.jsonl"
    assert fallback.exists()
    rec = json.loads(fallback.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["type"] == "turn.start"


def test_event_log_routes_to_existing_session_dir(_home):
    sess = _home / ".openprogram" / "sessions" / "s1"
    sess.mkdir(parents=True)
    bus = create_event_bus()
    bus.log_events = True
    bus.emit(make_event("turn.end", "system", {}, {"session": "s1"}))
    assert (sess / "events.jsonl").exists()


def test_gate_verdict_lands_on_the_log_line(_home):
    bus = create_event_bus()
    bus.log_events = True
    bus.subscribe_gate("turn.stop", lambda ev: "再跑一轮")
    out = bus.emit_gate(make_event("turn.stop", "system"))
    assert out.reasons == ["再跑一轮"]
    log = _home / ".openprogram" / "logs" / "events.jsonl"
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["gate"]["allowed"] is False
    assert rec["gate"]["reasons"] == ["再跑一轮"]
    assert rec["gate"]["subscribers"] == 1
    assert isinstance(rec["gate"]["duration_ms"], int)


def test_event_log_rotates_past_5mb(_home):
    log_dir = _home / ".openprogram" / "logs"
    log_dir.mkdir(parents=True)
    log = log_dir / "events.jsonl"
    log.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
    old_rotated = log_dir / "events.jsonl.1"
    old_rotated.write_text("old", encoding="utf-8")
    bus = create_event_bus()
    bus.log_events = True
    bus.emit(make_event("turn.start", "system"))
    assert old_rotated.read_text(encoding="utf-8") != "old"  # 旧 .1 被覆盖
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1  # 新文件只有这一行


# turn.stop 续轮辅助函数

@dataclass
class _Result:
    final_text: str = "done"
    user_msg_id: str = "u1"
    assistant_msg_id: str = "a1"
    failed: bool = False
    tool_calls: list = field(default_factory=list)


def _stop_req():
    from openprogram.agent.dispatcher.types import TurnRequest
    return TurnRequest(session_id="sess-stop-hook", user_text="hi",
                       agent_id="default", source="web")


@pytest.fixture()
def _singleton_gates_clean():
    from openprogram.events import get_event_bus
    bus = get_event_bus()
    with bus._lock:
        before = {t: list(fns) for t, fns in bus._gates.items()}
    yield bus
    with bus._lock:
        bus._gates.clear()
        bus._gates.update(before)


def test_stop_hook_allowed_returns_result_without_turns(_singleton_gates_clean):
    from openprogram.agent.dispatcher.stop_hook import continue_stop_hook_turns

    calls = []
    result = _Result()
    out = continue_stop_hook_turns(
        _stop_req(), result,
        run_turn=lambda req, **kw: calls.append(req) or _Result())
    assert out is result and calls == []


def test_stop_hook_denial_runs_continuation_then_allows(_singleton_gates_clean):
    from openprogram.agent.dispatcher.stop_hook import continue_stop_hook_turns

    bus = _singleton_gates_clean
    seen_payloads = []

    def gate(ev):
        seen_payloads.append(dict(ev.payload))
        return "还差收尾" if len(seen_payloads) == 1 else None

    bus.subscribe_gate("turn.stop", gate)
    ran = []

    def run_turn(req, **kw):
        ran.append(req)
        return _Result(final_text="第二轮", user_msg_id="u2",
                       assistant_msg_id="a2")

    out = continue_stop_hook_turns(_stop_req(), _Result(), run_turn=run_turn)
    assert len(ran) == 1
    assert ran[0].source == "hook_continue"
    assert ran[0].user_text == "[hook] 还差收尾。继续。"
    assert out.final_text == "第二轮"
    # 第一问 stop_hook_active=False，续轮后为 True
    assert seen_payloads[0]["stop_hook_active"] is False
    assert seen_payloads[1]["stop_hook_active"] is True
    assert seen_payloads[1]["last_text"] == "第二轮"


def test_stop_hook_no_numeric_cap(_singleton_gates_clean):
    """No runaway number — the stop_hook_active flag protocol replaces
    the old cap: the hook keeps seeing stop_hook_active=True and is the
    one expected to allow the stop. 15 denials → 15 continuations."""
    from openprogram.agent.dispatcher.stop_hook import (
        continue_stop_hook_turns,
    )

    bus = _singleton_gates_clean
    denials = {"n": 0}

    def gate(ev):
        if denials["n"] >= 15:
            return None
        denials["n"] += 1
        return "还不行"

    bus.subscribe_gate("turn.stop", gate)
    ran = []
    out = continue_stop_hook_turns(
        _stop_req(), _Result(),
        run_turn=lambda req, **kw: ran.append(req) or _Result())
    assert len(ran) == 15                     # past the old cap of 10
    assert out is not None


def test_stop_hook_failed_turn_skips_gate(_singleton_gates_clean):
    from openprogram.agent.dispatcher.stop_hook import continue_stop_hook_turns

    bus = _singleton_gates_clean
    asked = []
    bus.subscribe_gate("turn.stop", lambda ev: asked.append(1) or "拦")
    result = _Result(failed=True)
    out = continue_stop_hook_turns(
        _stop_req(), result, run_turn=lambda req, **kw: _Result())
    assert out is result and asked == []
