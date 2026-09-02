"""Canonical execution driver bridge for the existing Job worker.

This slice only establishes the Job execution contract.  Public job spawn and
dispatcher admission still use the legacy JobRunner path until their complete
cutover can be made atomic.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from openprogram.execution.attempts import AttemptRecord
from openprogram.execution.driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
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
    cancel_event: threading.Event | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.execution_id or not self.attempt_id or self.generation < 1:
            raise ValueError(
                "execution_id, attempt_id, and a positive generation are required"
            )


@dataclass(frozen=True)
class _WorkerBinding:
    cancel_event: threading.Event | None = None
    cancel_callback: Callable[[JobDriverHandle], object] | None = None
    terminate_callback: Callable[[JobDriverHandle, str], TerminationReceipt] | None = None


class JobDriver:
    """ExecutionDriver adapter for a Job's current physical attempt.

    Jobs intentionally expose no pause, step, steer, fork, retry, or safe
    point capability in this first production slice.  The driver only bridges
    the canonical attempt identity to the existing worker's cancellation
    event.  The event is never addressed by session or by a bare job id.
    """

    def __init__(
        self,
        *,
        execution_id: str,
        cancel_event: threading.Event | None = None,
        cancel_callback: Callable[[JobDriverHandle], object] | None = None,
        terminate_callback: Callable[[JobDriverHandle, str], TerminationReceipt]
        | None = None,
    ) -> None:
        if not execution_id:
            raise ValueError("execution_id must be non-empty")
        self.execution_id = execution_id
        self._default_worker = _WorkerBinding(
            cancel_event=cancel_event,
            cancel_callback=cancel_callback,
            terminate_callback=terminate_callback,
        )
        self._pending: dict[str, JobDriverHandle] = {}
        self._active: dict[str, JobDriverHandle] = {}
        self._workers: dict[tuple[str, str, int], _WorkerBinding] = {}
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
        return self.new_handle(
            attempt.attempt_id,
            attempt.generation,
            execution_id=attempt.execution_id,
        )

    def new_handle(
        self,
        attempt_id: str,
        generation: int,
        *,
        execution_id: str | None = None,
    ) -> JobDriverHandle:
        execution_id = execution_id or self.execution_id
        if execution_id != self.execution_id:
            raise DriverRegistryConflict(
                "execution_mismatch",
                "job handle belongs to another execution",
            )
        handle = JobDriverHandle(
            execution_id,
            attempt_id,
            generation,
            cancel_event=self._default_worker.cancel_event,
        )
        with self._lock:
            current = self._active.get(execution_id)
            if current is not None and (
                current.attempt_id == attempt_id
                and current.generation == generation
            ):
                return current
            pending = self._pending.get(execution_id)
            if pending is not None and (
                pending.attempt_id != attempt_id
                or pending.generation != generation
            ):
                raise DriverRegistryConflict(
                    "activation_pending",
                    "job driver already has a pending activation",
                )
            if pending is not None:
                return pending
            self._pending[execution_id] = handle
            if (
                self._default_worker.cancel_event is not None
                or self._default_worker.cancel_callback is not None
                or self._default_worker.terminate_callback is not None
            ):
                self._workers[
                    (execution_id, attempt_id, generation)
                ] = self._default_worker
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

    def bind_worker(
        self,
        handle: JobDriverHandle,
        *,
        cancel_event: threading.Event | None = None,
        cancel_callback: Callable[[JobDriverHandle], object] | None = None,
        terminate_callback: Callable[[JobDriverHandle, str], TerminationReceipt]
        | None = None,
    ) -> None:
        """Bind the exact worker cancellation and termination hooks."""
        self._require_known(handle)
        with self._lock:
            self._workers[
                (handle.execution_id, handle.attempt_id, handle.generation)
            ] = _WorkerBinding(
                cancel_event=cancel_event,
                cancel_callback=cancel_callback,
                terminate_callback=terminate_callback,
            )
            if cancel_event is not None:
                handle.cancel_event = cancel_event

    def activation_committed(self, binding: DriverBinding[JobDriverHandle]) -> None:
        """Publish a prepared handle only after DriverRegistry accepts it."""
        handle = binding.handle
        if (
            binding.driver is not self
            or binding.execution_id != handle.execution_id
            or binding.attempt_id != handle.attempt_id
            or binding.generation != handle.generation
        ):
            raise DriverRegistryConflict(
                "invalid_binding",
                "job activation binding does not match its handle",
            )
        with self._lock:
            pending = self._pending.get(handle.execution_id)
            if pending is not handle:
                if self._active.get(handle.execution_id) is handle:
                    return
                raise DriverRegistryConflict(
                    "stale_activation",
                    "job activation was not prepared by this driver",
                )
            self._active[handle.execution_id] = handle
            del self._pending[handle.execution_id]

    def activation_aborted(self, binding: DriverBinding[JobDriverHandle]) -> None:
        """Discard a prepared handle when registry fencing rejects it."""
        handle = binding.handle
        with self._lock:
            if self._pending.get(handle.execution_id) is handle:
                del self._pending[handle.execution_id]
                self._workers.pop(
                    (handle.execution_id, handle.attempt_id, handle.generation),
                    None,
                )

    def binding_unbound(self, binding: DriverBinding[JobDriverHandle]) -> None:
        """Retire the exact local handle when its canonical binding ends."""
        if binding.driver is self:
            self.retire(binding.handle)

    def _require_known(self, handle: JobDriverHandle) -> None:
        with self._lock:
            current = self._active.get(handle.execution_id)
            pending = self._pending.get(handle.execution_id)
        if current is not handle and pending is not handle:
            raise DriverRegistryConflict(
                "stale_attempt",
                "job handle does not match a prepared or active attempt",
            )

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
        worker = self._worker_for(handle)
        if worker.cancel_event is None and worker.cancel_callback is None:
            raise DriverRegistryConflict(
                "worker_not_bound",
                "job driver has no cancellation hook for this attempt",
            )
        if worker.cancel_callback is not None:
            result = worker.cancel_callback(handle)
            if result is False:
                raise DriverRegistryConflict(
                    "cancel_rejected",
                    "worker rejected cancellation for this attempt",
                )
        if worker.cancel_event is not None:
            worker.cancel_event.set()
        return DriverAck(
            command_id=command_id,
            attempt_id=handle.attempt_id,
            details={"execution_id": handle.execution_id},
        )

    async def inspect(self, handle: JobDriverHandle) -> RuntimeSnapshot:
        self._require_current(handle)
        worker = self._worker_for(handle)
        return RuntimeSnapshot(
            attempt_id=handle.attempt_id,
            state_schema_version=1,
            safe_point_kind=None,
            state={
                "cancel_requested": bool(
                    worker.cancel_event is not None and worker.cancel_event.is_set()
                ),
                "worker_bound": True,
            },
        )

    async def terminate(
        self, handle: JobDriverHandle, reason: str
    ) -> TerminationReceipt:
        self._require_current(handle)
        worker = self._worker_for(handle)
        if worker.terminate_callback is None:
            raise DriverRegistryConflict(
                "worker_not_bound",
                "job driver has no termination callback for this attempt",
            )
        receipt = worker.terminate_callback(handle, reason)
        if not isinstance(receipt, TerminationReceipt):
            raise DriverRegistryConflict(
                "invalid_termination_receipt",
                "worker termination callback must return TerminationReceipt",
            )
        if receipt.attempt_id != handle.attempt_id:
            raise DriverRegistryConflict(
                "invalid_termination_receipt",
                "termination receipt belongs to another attempt",
            )
        return receipt

    def _worker_for(self, handle: JobDriverHandle) -> _WorkerBinding:
        self._require_current(handle)
        with self._lock:
            worker = self._workers.get(
                (handle.execution_id, handle.attempt_id, handle.generation)
            )
        if worker is None:
            raise DriverRegistryConflict(
                "worker_not_bound",
                "job driver has no worker binding for this attempt",
            )
        return worker

    def retire(self, handle: JobDriverHandle) -> bool:
        """Release a finished handle without affecting a replacement owner."""
        with self._lock:
            if self._active.get(handle.execution_id) is not handle:
                return False
            del self._active[handle.execution_id]
            self._workers.pop(
                (handle.execution_id, handle.attempt_id, handle.generation), None
            )
            return True


class JobActivationBridge:
    """Adapt ``JobDriver.activate`` to RuntimeControlService's callback form."""

    def __init__(self, driver: JobDriver) -> None:
        self.driver = driver

    async def activate(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput,
    ) -> DriverBinding[JobDriverHandle]:
        handle = await self.driver.activate(attempt, activation)
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=self.driver,
            handle=handle,
        )


__all__ = ["JobActivationBridge", "JobDriver", "JobDriverHandle"]
