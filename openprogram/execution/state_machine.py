"""Pure lifecycle and command validation for execution control."""

from __future__ import annotations

from collections.abc import Collection

from .model import (
    CommandKind,
    ExecutionStatus,
    TERMINAL_EXECUTION_STATUSES,
)


class InvalidTransition(ValueError):
    def __init__(self, current: ExecutionStatus, target: ExecutionStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"invalid execution transition: {current.value} -> {target.value}"
        )


class InvalidCommand(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.QUEUED: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.PAUSED,
            ExecutionStatus.CANCELLING,
            ExecutionStatus.FAILED,
            ExecutionStatus.INTERRUPTED,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.PAUSING,
            ExecutionStatus.CANCELLING,
            ExecutionStatus.RECONCILIATION_REQUIRED,
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.INTERRUPTED,
        }
    ),
    ExecutionStatus.PAUSING: frozenset(
        {
            ExecutionStatus.PAUSED,
            ExecutionStatus.CANCELLING,
            ExecutionStatus.RECONCILIATION_REQUIRED,
            ExecutionStatus.FAILED,
            ExecutionStatus.INTERRUPTED,
        }
    ),
    ExecutionStatus.PAUSED: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING}
    ),
    ExecutionStatus.CANCELLING: frozenset(
        {
            ExecutionStatus.CANCELLED,
            ExecutionStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionStatus.RECONCILIATION_REQUIRED: frozenset(
        {
            ExecutionStatus.PAUSED,
            ExecutionStatus.CANCELLING,
            ExecutionStatus.FAILED,
            ExecutionStatus.INTERRUPTED,
        }
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.INTERRUPTED: frozenset(),
}


_COMMAND_STATES: dict[CommandKind, frozenset[ExecutionStatus]] = {
    CommandKind.PAUSE: frozenset({ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}),
    CommandKind.CONTINUE: frozenset({ExecutionStatus.PAUSED}),
    CommandKind.STEP: frozenset({ExecutionStatus.PAUSED}),
    CommandKind.STEER: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.PAUSING,
            ExecutionStatus.PAUSED,
        }
    ),
    CommandKind.CANCEL: frozenset(
        set(ExecutionStatus) - set(TERMINAL_EXECUTION_STATUSES)
    ),
    CommandKind.FORK: frozenset(
        {
            ExecutionStatus.PAUSED,
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.INTERRUPTED,
        }
    ),
    CommandKind.RETRY: frozenset({ExecutionStatus.FAILED, ExecutionStatus.INTERRUPTED}),
    # A wait outcome changes only its execution-owned wait record.  It does
    # not invent a second lifecycle state, and may be submitted while the
    # execution has released its owner and is paused.
    CommandKind.WAIT_ANSWER: frozenset(
        set(ExecutionStatus) - set(TERMINAL_EXECUTION_STATUSES)
    ),
    CommandKind.WAIT_DECLINE: frozenset(
        set(ExecutionStatus) - set(TERMINAL_EXECUTION_STATUSES)
    ),
}


_COMMAND_CAPABILITY = {
    CommandKind.PAUSE: "pause",
    CommandKind.CONTINUE: "pause",
    CommandKind.STEP: "step",
    CommandKind.STEER: "steer",
    CommandKind.FORK: "fork",
    CommandKind.RETRY: "retry",
}


def validate_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    if target not in _TRANSITIONS[current]:
        raise InvalidTransition(current, target)


def validate_command(
    kind: CommandKind,
    status: ExecutionStatus,
    capabilities: Collection[str],
) -> None:
    if status in TERMINAL_EXECUTION_STATUSES and kind is CommandKind.CANCEL:
        raise InvalidCommand("terminal", f"{status.value} execution is terminal")
    if status not in _COMMAND_STATES[kind]:
        raise InvalidCommand(
            "invalid_state",
            f"{kind.value} is invalid while execution is {status.value}",
        )
    required = _COMMAND_CAPABILITY.get(kind)
    if required is not None and required not in capabilities:
        raise InvalidCommand(
            "unsupported",
            f"execution does not declare the {required} capability",
        )
