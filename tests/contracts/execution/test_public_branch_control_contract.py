"""Public execution branch-control contract.

The transport entry points are intentionally checked here instead of accepting
the historical session-scoped ``steer`` message.  Every caller must use the
same exact execution envelope.
"""
from __future__ import annotations

from openprogram.webui.ws_actions import runtime


def test_branch_controls_have_only_canonical_execution_actions() -> None:
    assert "steer" not in runtime.ACTIONS
    for action in ("execution.steer", "execution.fork", "execution.retry"):
        assert action in runtime.ACTIONS


def test_public_envelope_rejects_spoofed_scope_and_invalid_branch_payloads() -> None:
    validate = runtime.validate_execution_command_request

    assert validate(
        {
            "type": "execution.command",
            "action": "execution.steer",
            "command_id": "steer-1",
            "execution_id": "exec-1",
            "expected_version": 7,
            "payload": {"message": "Use the supplied API response."},
        },
        "steer",
    ) is None
    assert validate(
        {
            "type": "execution.command",
            "action": "execution.steer",
            "command_id": "steer-1",
            "execution_id": "exec-1",
            "expected_version": 7,
            "session_id": "forged",
            "payload": {"message": "x"},
        },
        "steer",
    ) == "invalid_command"
    assert validate(
        {
            "type": "execution.command",
            "action": "execution.fork",
            "command_id": "fork-1",
            "execution_id": "exec-1",
            "expected_version": 7,
            "payload": {"checkpoint_id": "checkpoint-1"},
        },
        "fork",
    ) == "invalid_payload"
    assert validate(
        {
            "type": "execution.command",
            "action": "execution.retry",
            "command_id": "retry-1",
            "execution_id": "exec-1",
            "expected_version": 7,
            "payload": {"checkpoint_id": "checkpoint-1", "force": True},
        },
        "retry",
    ) == "invalid_payload"
