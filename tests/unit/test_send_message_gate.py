"""C7 — send_message is subject to the tool.before gate (watch mode).

Every tool passes through decide_tool_gate before execution
(agent_loop._execute_tool_calls), so a watch/deny policy can block a
cross-branch delivery and require confirmation. This locks that guarantee:
a gate that denies send_message is honoured, and doesn't touch other
tools.

See docs/design/runtime/agent-collaboration.md (C7).
"""
from __future__ import annotations

from openprogram.events import make_event
from openprogram.events import register_tool_gate, decide_tool_gate


def _before(tool_name: str, args=None):
    return make_event("tool.before", "agent",
                      {"tool": tool_name, "args": args or {}})


def test_gate_can_deny_send_message():
    def gate(ev):
        if ev.payload.get("tool") == "send_message":
            return "cross-branch delivery requires confirmation (attended off)"
        return None

    unreg = register_tool_gate(gate)
    try:
        denial = decide_tool_gate(_before("send_message", {"to": "p2:h"}))
        assert denial is not None
        assert "confirmation" in denial
        # other tools are unaffected by this gate
        assert decide_tool_gate(_before("read", {"path": "x"})) is None
    finally:
        unreg()


def test_no_gate_allows():
    # With no gates registered, send_message is allowed (default).
    assert decide_tool_gate(_before("send_message")) is None
