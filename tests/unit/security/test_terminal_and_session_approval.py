"""Terminal input follows the permission mode; polling a command session never asks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _decided(decision):
    return decision.outcome, decision.code


@pytest.fixture
def decide(monkeypatch):
    from openprogram.runtime.permissions import tool_authority
    from openprogram.runtime.permissions import identity, policy
    from openprogram.programs._host.runtime import get
    monkeypatch.setattr(identity, "scope_violation", lambda *a, **k: None)
    monkeypatch.setattr(policy, "can_approve", lambda req: True)
    monkeypatch.setattr(tool_authority, "decide_tool_authority",
                        lambda *a, **k: SimpleNamespace(allowed=True, reason_code="", capability=""))

    def run(tool, args, *, mode="bypass", allow=()):
        rules = SimpleNamespace(allow=list(allow), ask=[], deny=[])
        req = SimpleNamespace(permission_mode=mode, permission_rules=rules, session_id="s-test",
                              source="web", surface_context=None)
        return _decided(policy.permission_decision(get(tool), req, args))
    return run


@pytest.mark.parametrize("terminal_id", ["terminal:desktop:shell", "terminal-resource:" + "0" * 32])
def test_terminal_input_follows_the_permission_mode(decide, terminal_id):
    args = {"action": "input", "terminal_id": terminal_id, "data": "ls\r"}
    assert decide("terminal_use", args) == ("allow", "MODE_UNRESTRICTED")
    assert decide("terminal_use", args, mode="auto") == ("auto", "AUTO_CLASSIFY")
    assert decide("terminal_use", args, mode="default") == ("ask", "MODE_APPROVAL")


@pytest.mark.parametrize("chars", ["", "\x03"])
def test_polling_or_interrupting_a_command_session_does_not_ask(decide, chars):
    assert decide("write_stdin", {"session_id": "abc", "chars": chars}, mode="default") == ("allow", "SESSION_POLL")


def test_typing_into_a_command_session_follows_the_mode(decide):
    assert decide("write_stdin", {"session_id": "abc", "chars": "y\n"}, mode="default") == ("ask", "MODE_APPROVAL")
    assert decide("write_stdin", {"session_id": "abc", "chars": "y\n"}, mode="auto") == ("auto", "AUTO_CLASSIFY")
