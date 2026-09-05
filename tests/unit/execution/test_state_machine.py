from __future__ import annotations

import pytest

from openprogram.execution.model import CommandKind, ExecutionStatus
from openprogram.execution.state_machine import (
    InvalidCommand,
    InvalidTransition,
    validate_command,
    validate_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING),
        (ExecutionStatus.QUEUED, ExecutionStatus.PAUSED),
        (ExecutionStatus.RUNNING, ExecutionStatus.PAUSING),
        (ExecutionStatus.PAUSING, ExecutionStatus.PAUSED),
        (ExecutionStatus.PAUSED, ExecutionStatus.RUNNING),
        (ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING),
        (ExecutionStatus.PAUSED, ExecutionStatus.CANCELLING),
        (ExecutionStatus.CANCELLING, ExecutionStatus.CANCELLED),
        (
            ExecutionStatus.RECONCILIATION_REQUIRED,
            ExecutionStatus.PAUSED,
        ),
    ],
)
def test_execution_state_machine_accepts_only_declared_edges(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> None:
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ExecutionStatus.RUNNING, ExecutionStatus.PAUSED),
        (ExecutionStatus.PAUSED, ExecutionStatus.COMPLETED),
        (ExecutionStatus.CANCELLING, ExecutionStatus.RUNNING),
        (ExecutionStatus.COMPLETED, ExecutionStatus.RUNNING),
        (ExecutionStatus.CANCELLED, ExecutionStatus.CANCELLING),
        (ExecutionStatus.FAILED, ExecutionStatus.PAUSED),
    ],
)
def test_execution_state_machine_rejects_skipped_and_terminal_edges(
    current: ExecutionStatus,
    target: ExecutionStatus,
) -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(current, target)


@pytest.mark.parametrize(
    ("kind", "status", "capabilities"),
    [
        (CommandKind.PAUSE, ExecutionStatus.RUNNING, {"pause"}),
        (CommandKind.PAUSE, ExecutionStatus.QUEUED, {"pause"}),
        (CommandKind.CONTINUE, ExecutionStatus.PAUSED, {"pause"}),
        (CommandKind.STEP, ExecutionStatus.PAUSED, {"step"}),
        (CommandKind.STEER, ExecutionStatus.RUNNING, {"steer"}),
        (CommandKind.STEER, ExecutionStatus.PAUSING, {"steer"}),
        (CommandKind.STEER, ExecutionStatus.PAUSED, {"steer"}),
        (CommandKind.CANCEL, ExecutionStatus.RUNNING, set()),
        (CommandKind.CANCEL, ExecutionStatus.RECONCILIATION_REQUIRED, set()),
        (CommandKind.FORK, ExecutionStatus.PAUSED, {"fork"}),
        (CommandKind.FORK, ExecutionStatus.COMPLETED, {"fork"}),
        (CommandKind.RETRY, ExecutionStatus.FAILED, {"retry"}),
        (CommandKind.RETRY, ExecutionStatus.INTERRUPTED, {"retry"}),
    ],
)
def test_command_legality_is_centralized(
    kind: CommandKind,
    status: ExecutionStatus,
    capabilities: set[str],
) -> None:
    validate_command(kind, status, capabilities)


@pytest.mark.parametrize(
    ("kind", "status", "capabilities", "code"),
    [
        (CommandKind.PAUSE, ExecutionStatus.RUNNING, set(), "unsupported"),
        (CommandKind.CONTINUE, ExecutionStatus.RUNNING, {"pause"}, "invalid_state"),
        (CommandKind.STEP, ExecutionStatus.PAUSED, {"pause"}, "unsupported"),
        (CommandKind.STEER, ExecutionStatus.CANCELLED, {"steer"}, "invalid_state"),
        (CommandKind.CANCEL, ExecutionStatus.COMPLETED, set(), "terminal"),
        (CommandKind.FORK, ExecutionStatus.RUNNING, {"fork"}, "invalid_state"),
        (CommandKind.RETRY, ExecutionStatus.FAILED, set(), "unsupported"),
    ],
)
def test_command_legality_reports_stable_rejection_codes(
    kind: CommandKind,
    status: ExecutionStatus,
    capabilities: set[str],
    code: str,
) -> None:
    with pytest.raises(InvalidCommand) as caught:
        validate_command(kind, status, capabilities)
    assert caught.value.code == code
