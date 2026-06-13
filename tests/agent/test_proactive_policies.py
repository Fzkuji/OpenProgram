"""MVP 三条 policy 的触发与误报防护。"""
from __future__ import annotations

from openprogram.agent.event_bus import make_event
from openprogram.proactive.actions import Gate, Inject, Notify
from openprogram.proactive.policies.dangerous_command import DangerousCommandGuard
from openprogram.proactive.policies.unvalidated_completion import UnvalidatedCompletionNudge
from openprogram.proactive.policies.test_gap import TestGapWatcher
from openprogram.proactive.state import SessionState


def _before(cmd, tool="bash"):
    return make_event("tool.before", "agent",
                      {"tool": tool, "args": {"command": cmd}}, {"session": "s"})


# ─── DangerousCommandGuard：拦危险、放日常 ────────────────────────────────────

def test_guard_blocks_rm_rf_root():
    g = DangerousCommandGuard()
    for cmd in ("rm -rf /", "rm -rf ~", "rm -rf /etc/x", "rm -rf $HOME"):
        a = g.evaluate(_before(cmd), SessionState())
        assert isinstance(a, Gate) and a.verdict == "ask", cmd


def test_guard_allows_daily_rm():
    g = DangerousCommandGuard()
    for cmd in ("rm -rf node_modules", "rm -rf /tmp/build", "rm -rf ./dist", "ls -la"):
        assert g.evaluate(_before(cmd), SessionState()) is None, cmd


def test_guard_force_push_protected_vs_feature():
    g = DangerousCommandGuard()
    assert g.evaluate(_before("git push --force origin main"), SessionState()).verdict == "ask"
    assert g.evaluate(_before("git push --force origin my-feature"), SessionState()) is None


def test_guard_namespace_and_destroy():
    g = DangerousCommandGuard()
    assert g.evaluate(_before("kubectl delete namespace prod"), SessionState()).verdict == "ask"
    assert g.evaluate(_before("kubectl delete pod foo"), SessionState()) is None
    assert g.evaluate(_before("terraform destroy"), SessionState()).verdict == "ask"


def test_guard_ignores_non_bash():
    g = DangerousCommandGuard()
    ev = make_event("tool.before", "agent",
                    {"tool": "read", "args": {"command": "rm -rf /"}}, {"session": "s"})
    assert g.evaluate(ev, SessionState()) is None


# ─── UnvalidatedCompletionNudge ───────────────────────────────────────────────

def test_nudge_fires_on_change_without_verification():
    p = UnvalidatedCompletionNudge()
    st = SessionState(turn_has_file_change=True, turn_has_verification=False)
    ev = make_event("model.response_completed", "agent", {}, {"session": "s"})
    a = p.evaluate(ev, st)
    assert isinstance(a, Inject) and "验证" in a.text


def test_nudge_silent_when_verified():
    p = UnvalidatedCompletionNudge()
    st = SessionState(turn_has_file_change=True, turn_has_verification=True)
    ev = make_event("model.response_completed", "agent", {}, {"session": "s"})
    assert p.evaluate(ev, st) is None


def test_nudge_silent_when_no_change():
    p = UnvalidatedCompletionNudge()
    st = SessionState(turn_has_file_change=False)
    ev = make_event("model.response_completed", "agent", {}, {"session": "s"})
    assert p.evaluate(ev, st) is None


# ─── TestGapWatcher ───────────────────────────────────────────────────────────

def _completed():
    return make_event("model.response_completed", "agent", {}, {"session": "s"})


def test_gap_fires_core_change_no_test():
    p = TestGapWatcher()
    st = SessionState(turn_has_file_change=True,
                      changed_files={"openprogram/agent/loop.py"})
    a = p.evaluate(_completed(), st)
    assert isinstance(a, Notify) and "测试" in a.message


def test_gap_silent_when_test_also_changed():
    p = TestGapWatcher()
    st = SessionState(turn_has_file_change=True,
                      changed_files={"openprogram/agent/loop.py",
                                     "tests/agent/test_loop.py"})
    assert p.evaluate(_completed(), st) is None


def test_gap_silent_for_noncore_change():
    p = TestGapWatcher()
    st = SessionState(turn_has_file_change=True,
                      changed_files={"README.md", "docs/x.md"})
    assert p.evaluate(_completed(), st) is None


def test_gap_silent_when_no_change_this_turn():
    p = TestGapWatcher()
    st = SessionState(turn_has_file_change=False,
                      changed_files={"openprogram/agent/loop.py"})
    assert p.evaluate(_completed(), st) is None
