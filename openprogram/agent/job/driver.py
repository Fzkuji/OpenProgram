"""Canonical execution driver bridge for the existing Job worker.

This slice only establishes the Job execution contract.  Public job spawn and
dispatcher admission still use the legacy JobRunner path until their complete
cutover can be made atomic.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from openprogram.execution.attempts import AttemptRecord
from openprogram.execution.driver import (
    ActivationInput,
    DriverAck,
    DriverRegistryConflict,
    RuntimeSnapshot,
    TerminationReceipt,
)
from openprogram.execution.model import CapabilitySet


@dataclass
class JobDriverHandle:
    """Process-local cancellation handle for one canonical Job attempt."""

    execution_id: str
    attempt_id: str
    generation: int
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def __post_init__(self) -> None:
        if not self.execution_id or not self.attempt_id or self.generation < 1:
            raise ValueError(
                "execution_id, attempt_id, and a positive generation are required"
            )


class JobDriver:
    """ExecutionDriver adapter for a Job's current physical attempt.

    Jobs intentionally expose no pause, step, steer, fork, retry, or safe
    point capability in this first production slice.  The driver only bridges
    the canonical attempt identity to the existing worker's cancellation
    event.  The event is never addressed by session or by a bare job id.
    """

    def __init__(self, *, execution_id: str | None = None) -> None:
        self.execution_id = execution_id
        self._active: dict[str, JobDriverHandle] = {}
        self._lock = threading.RLock()

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()

    async def activate(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput,
    ) -> JobDriverHandle:
        del activation
        if self.execution_id is not None and attempt.execution_id != self.execution_id:
            raise DriverRegistryConflict(
                "execution_mismatch",
                "job activation belongs to another execution",
            )
        return self.new_handle(attempt.attempt_id, attempt.generation, execution_id=attempt.execution_id)

    def new_handle(
        self,
        attempt_id: str,
        generation: int,
        *,
        execution_id: str | None = None,
    ) -> JobDriverHandle:
        execution_id = execution_id or self.execution_id
        if execution_id is None:
            raise ValueError("execution_id is required for a Job driver handle")
        if self.execution_id is not None and execution_id != self.execution_id:
            raise DriverRegistryConflict(
                "execution_mismatch",
                "job handle belongs to another execution",
            )
        handle = JobDriverHandle(execution_id, attempt_id, generation)
        with self._lock:
            current = self._active.get(execution_id)
            if current is not None and (
                current.attempt_id == attempt_id
                and current.generation == generation
            ):
                return current
            self._active[execution_id] = handle
        return handle

    def handle_for(
        self, attempt_id: str, generation: int
    ) -> JobDriverHandle | None:
        with self._lock:
            handle = self._active.get(self.execution_id or "")
            if handle is None:
                return None
            if handle.attempt_id != attempt_id or handle.generation != generation:
                return None
            return handle

    def _require_current(self, handle: JobDriverHandle) -> None:
        with self._lock:
            current = self._active.get(handle.execution_id)
        if current is not handle:
            raise DriverRegistryConflict(
                "stale_attempt",
                "job handle does not match the current attempt",
            )

    async def request_pause(
        self, handle: JobDriverHandle, command_id: str
    ) -> DriverAck:
        del handle, command_id
        raise DriverRegistryConflict(
            "unsupported",
            "Job driver does not expose pause or safe points",
        )

    async def request_cancel(
        self, handle: JobDriverHandle, command_id: str
    ) -> DriverAck:
        if not command_id:
            raise DriverRegistryConflict("invalid_command", "command_id is required")
        self._require_current(handle)
        handle.cancel_event.set()
        return DriverAck(
            command_id=command_id,
            attempt_id=handle.attempt_id,
            details={"execution_id": handle.execution_id},
        )

    async def inspect(self, handle: JobDriverHandle) -> RuntimeSnapshot:
        self._require_current(handle)
        return RuntimeSnapshot(
            attempt_id=handle.attempt_id,
            state_schema_version=1,
            safe_point_kind=None,
            state={"cancel_requested": handle.cancel_event.is_set()},
        )

    async def terminate(
        self, handle: JobDriverHandle, reason: str
    ) -> TerminationReceipt:
        self._require_current(handle)
        handle.cancel_event.set()
        return TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=True,
            reason=reason,
            details={"execution_id": handle.execution_id},
        )

    def retire(self, handle: JobDriverHandle) -> bool:
        """Release a finished handle without affecting a replacement owner."""
        with self._lock:
            if self._active.get(handle.execution_id) is not handle:
                return False
            del self._active[handle.execution_id]
            return True


class JobActivationBridge:
    """Adapt ``JobDriver.activate`` to RuntimeControlService's callback form."""

    def __init__(self, driver: JobDriver) -> None:
        self.driver = driver

    async def activate(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput,
    ) -> tuple[JobDriver, JobDriverHandle]:
        handle = await self.driver.activate(attempt, activation)
        return self.driver, handle


__all__ = ["JobActivationBridge", "JobDriver", "JobDriverHandle"]
