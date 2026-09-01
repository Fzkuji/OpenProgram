"""Driver contract and process-local live-owner registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

from .attempts import AttemptRecord
from .checkpoints import CheckpointManifest
from .model import CapabilitySet


HandleT = TypeVar("HandleT")


@dataclass(frozen=True)
class DriverAck:
    """Confirmation that a command reached the exact live attempt."""

    command_id: str
    attempt_id: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Committed driver inspection data; never an arbitrary stack snapshot."""

    attempt_id: str
    state_schema_version: int
    safe_point_kind: str | None
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TerminationReceipt:
    attempt_id: str
    terminated: bool
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)


class ExecutionDriver(Protocol[HandleT]):
    """Execution-kind adapter used only through RuntimeControlService."""

    def capabilities(self) -> CapabilitySet: ...

    async def activate(
        self,
        attempt: AttemptRecord,
        checkpoint: CheckpointManifest | None,
    ) -> HandleT: ...

    async def request_pause(
        self,
        handle: HandleT,
        command_id: str,
    ) -> DriverAck: ...

    async def request_cancel(
        self,
        handle: HandleT,
        command_id: str,
    ) -> DriverAck: ...

    async def inspect(self, handle: HandleT) -> RuntimeSnapshot: ...

    async def terminate(
        self,
        handle: HandleT,
        reason: str,
    ) -> TerminationReceipt: ...


@dataclass(frozen=True)
class DriverBinding(Generic[HandleT]):
    execution_id: str
    attempt_id: str
    generation: int
    driver: ExecutionDriver[HandleT]
    handle: HandleT

    def __post_init__(self) -> None:
        if not self.execution_id or not self.attempt_id or self.generation < 1:
            raise ValueError(
                "execution_id, attempt_id, and a positive generation are required"
            )


class DriverRegistryConflict(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DriverRegistry:
    """Holds live handles; durable identity and lifecycle remain in the store."""

    def __init__(self) -> None:
        self._bindings: dict[str, DriverBinding[Any]] = {}
        self._lock = RLock()
        self._owner_resolver: Callable[[str], tuple[str, int] | None] | None = None

    def set_owner_resolver(
        self, resolver: Callable[[str], tuple[str, int] | None]
    ) -> None:
        """Attach the durable owner fence used when replacing stale bindings."""
        with self._lock:
            self._owner_resolver = resolver

    def bind(self, binding: DriverBinding[HandleT]) -> DriverBinding[HandleT]:
        with self._lock:
            current = self._bindings.get(binding.execution_id)
            if current is binding:
                return binding
            durable_owner = None
            if self._owner_resolver is not None:
                durable_owner = self._owner_resolver(binding.execution_id)
                if durable_owner != (binding.attempt_id, binding.generation):
                    raise DriverRegistryConflict(
                        "stale_owner",
                        "driver binding does not match the durable execution owner",
                    )
            if current is not None:
                if self._owner_resolver is not None and durable_owner != (
                    current.attempt_id,
                    current.generation,
                ):
                    self._bindings[binding.execution_id] = binding
                    return binding
                raise DriverRegistryConflict(
                    "owner_exists",
                    "execution already has a live driver binding",
                )
            self._bindings[binding.execution_id] = binding
            return binding

    def resolve(
        self,
        execution_id: str,
        *,
        attempt_id: str | None = None,
        generation: int | None = None,
    ) -> DriverBinding[Any]:
        with self._lock:
            binding = self._bindings.get(execution_id)
            if binding is None:
                raise DriverRegistryConflict(
                    "not_found", "execution has no live driver binding"
                )
            if (
                attempt_id is not None
                and binding.attempt_id != attempt_id
                or generation is not None
                and binding.generation != generation
            ):
                raise DriverRegistryConflict(
                    "stale_binding",
                    "live driver binding does not match the requested attempt",
                )
            return binding

    def unbind(
        self,
        execution_id: str,
        *,
        attempt_id: str,
        generation: int,
    ) -> bool:
        with self._lock:
            binding = self._bindings.get(execution_id)
            if binding is None:
                return False
            if binding.attempt_id != attempt_id or binding.generation != generation:
                return False
            del self._bindings[execution_id]
            return True

    def snapshot(self) -> tuple[DriverBinding[Any], ...]:
        with self._lock:
            return tuple(self._bindings[key] for key in sorted(self._bindings))
