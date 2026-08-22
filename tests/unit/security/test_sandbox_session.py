"""Session override resolution and turn-policy snapshot."""
from __future__ import annotations

import pytest

from openprogram import sandbox
from openprogram.sandbox import (
    MODE_WORKSPACE_WRITE,
    resolve_policy,
    turn_policy,
)


@pytest.fixture
def cfg(monkeypatch):
    state: dict = {}
    monkeypatch.setattr("openprogram.setup._read_config", lambda: state)
    monkeypatch.setattr(
        sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY,
    )
    token = sandbox._execution_policy_override.set(sandbox._NO_PROCESS_POLICY)
    try:
        yield state
    finally:
        sandbox._execution_policy_override.reset(token)


def test_session_override_beats_configured_mode(cfg):
    cfg["sandbox"] = {"mode": "danger-full-access"}
    assert resolve_policy() is None
    assert resolve_policy(session_enabled=True) is not None

    cfg["sandbox"] = {"mode": MODE_WORKSPACE_WRITE}
    assert resolve_policy() is not None
    assert resolve_policy(session_enabled=False) is None
    assert resolve_policy(session_enabled=None) is not None


def test_turn_snapshot_ignores_later_toggle_and_cannot_relax(cfg):
    cfg["sandbox"] = {"mode": MODE_WORKSPACE_WRITE}
    with turn_policy(session_enabled=True):
        cfg["sandbox"]["mode"] = "danger-full-access"
        assert resolve_policy() is not None
        assert resolve_policy(session_enabled=False) is not None
        with turn_policy(session_enabled=False):
            assert resolve_policy() is not None
    assert resolve_policy() is None
