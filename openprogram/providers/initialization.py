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
    try:
        message = str(exc)
    except Exception:
        return "<message unavailable>"
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
    failure: _Failure | None = None
    control: BaseException | None = None
    try:
        _register_builtins()
        stage = "record_replay"
        mode = _activate_record_replay()
    except BaseException as exc:
        control = exc if isinstance(exc, (KeyboardInterrupt, SystemExit)) else None
        try:
            cause_message = _safe_message(exc)
        except BaseException as descriptor_error:
            control = descriptor_error
            cause_message = "<message unavailable>"
        failure = _Failure(
            stage=("initialization_interrupted" if control is not None else stage),
            cause_type=type(control or exc).__name__,
            cause_message=cause_message,
        )
        with _condition:
            _failure = failure
            _initializing_thread = None
            _state = "FAILED"
            _condition.notify_all()
    if control is not None:
        raise control
    if failure is not None:
        raise failure.error()

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
