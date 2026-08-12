"""Explicit, process-wide provider runtime initialization."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderRuntimeSnapshot:
    mode: str


@dataclass(frozen=True)
class _Failure:
    stage: str
    cause_type: str
    cause_message: str

    def error(self) -> "ProviderRuntimeInitializationError":
        return ProviderRuntimeInitializationError(
            self.stage, self.cause_type, self.cause_message
        )


class ProviderRuntimeInitializationError(RuntimeError):
    """The process-wide provider runtime could not be initialized."""

    def __init__(self, stage: str, cause_type: str, cause_message: str) -> None:
        self.stage = stage
        self.cause_type = cause_type
        self.cause_message = cause_message
        super().__init__(
            f"provider runtime initialization failed during {stage}: "
            f"{cause_type}: {cause_message}"
        )


_condition = threading.Condition()
_state = "NEW"
_snapshot: ProviderRuntimeSnapshot | None = None
_failure: _Failure | None = None
_initializing_thread: int | None = None


def _register_builtins() -> None:
    from .register import register_builtins

    register_builtins()


def _activate_record_replay() -> str:
    from .recording import activate_record_replay_from_config

    return activate_record_replay_from_config() or "off"


def _safe_message(exc: BaseException) -> str:
    message = str(exc)
    try:
        from .recording import remove_secret_values

        message = remove_secret_values(message)
    except Exception:
        pass
    return message[:500]


def initialize_provider_runtime() -> ProviderRuntimeSnapshot:
    """Initialize built-ins and record/replay exactly once for this process."""
    global _failure, _initializing_thread, _snapshot, _state

    ident = threading.get_ident()
    with _condition:
        while _state == "INITIALIZING":
            if _initializing_thread == ident:
                raise RuntimeError("recursive provider runtime initialization")
            _condition.wait()
        if _state == "READY":
            assert _snapshot is not None
            return _snapshot
        if _state == "FAILED":
            assert _failure is not None
            raise _failure.error()
        _state = "INITIALIZING"
        _initializing_thread = ident

    stage = "builtins"
    try:
        _register_builtins()
        stage = "record_replay"
        mode = _activate_record_replay()
    except BaseException as exc:
        failure = _Failure(
            stage=("initialization_interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else stage),
            cause_type=type(exc).__name__,
            cause_message=_safe_message(exc),
        )
        with _condition:
            _failure = failure
            _initializing_thread = None
            _state = "FAILED"
            _condition.notify_all()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise failure.error() from exc

    snapshot = ProviderRuntimeSnapshot(mode=mode)
    with _condition:
        _snapshot = snapshot
        _initializing_thread = None
        _state = "READY"
        _condition.notify_all()
    return snapshot


__all__ = [
    "ProviderRuntimeInitializationError",
    "ProviderRuntimeSnapshot",
    "initialize_provider_runtime",
]
