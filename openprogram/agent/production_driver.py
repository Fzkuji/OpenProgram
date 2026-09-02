"""Internal production driver for canonical Agent executions.

This module is deliberately not wired into the public turn entry points yet.
It provides the first safe activation boundary: an immutable admission input is
resolved into the existing dispatcher request, a live owner is bound to one
attempt generation, and completion is written through the canonical control
service only.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import threading
from dataclasses import dataclass, fields
from typing import Any, Callable, Mapping

from openprogram.execution.attempts import AttemptRecord, AttemptStatus, AttemptStore
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
    RuntimeSnapshot,
    TerminationReceipt,
)
from openprogram.execution.model import CapabilitySet, ExecutionStatus, TERMINAL_EXECUTION_STATUSES
from openprogram.execution.store import ExecutionStore


class AgentDriverError(RuntimeError):
    """A production Agent driver operation cannot be performed."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AgentDriverHandle:
    """Exact live owner identity and its cooperative completion task."""

    execution_id: str
    attempt_id: str
    generation: int
    session_id: str
    cancel_event: threading.Event
    done: asyncio.Future[Any]


InputResolver = Callable[[Any], Mapping[str, Any]]
TurnRunner = Callable[..., Any]


class AgentActivationService:
    """Resolve one immutable admission input into an existing Agent turn."""

    def __init__(self, input_resolver: InputResolver):
        if not callable(input_resolver):
            raise TypeError("input_resolver must be callable")
        self._input_resolver = input_resolver

    def build_request(
        self,
        record: Any,
        activation: ActivationInput | None,
    ) -> Any:
        if activation is not None and (
            activation.checkpoint is not None or activation.steer_inputs
        ):
            raise AgentDriverError(
                "unsupported_activation_state",
                "Agent driver does not support checkpoint or steering activation",
            )
        payload = self._input_resolver(record)
        if not isinstance(payload, Mapping):
            raise AgentDriverError(
                "invalid_input",
                "Agent admission input must resolve to an object",
            )
        # The resolver is an external durable-input boundary. Copy the full
        # payload before constructing the mutable TurnRequest so later changes
        # to a cache or transport object cannot alter the admitted turn.
        values = copy.deepcopy(dict(payload))
        from openprogram.agent.dispatcher.types import TurnRequest

        request_fields = frozenset(field.name for field in fields(TurnRequest))
        unknown = set(values) - request_fields
        if unknown:
            raise AgentDriverError(
                "invalid_input",
                f"Agent admission input has unknown fields: {sorted(unknown)}",
            )
        supplied_session = values.pop("session_id", None)
        if supplied_session is not None and supplied_session != record.session_id:
            raise AgentDriverError(
                "input_session_mismatch",
                "Agent admission input belongs to another session",
            )
        for required in ("user_text", "agent_id", "source"):
            if not values.get(required):
                raise AgentDriverError(
                    "invalid_input",
                    f"Agent admission input requires {required}",
                )
        return TurnRequest(session_id=record.session_id, **values)


class AgentProductionDriver:
    """Internal Agent execution driver with exact owner fencing.

    The driver supports cancellation as a cooperative signal. It intentionally
    advertises no pause, step, steer, fork, retry, or safe-point capability in
    this first production slice.
    """

    def __init__(
        self,
        executions: ExecutionStore | None,
        *,
        input_resolver: InputResolver,
        turn_runner: TurnRunner | None = None,
        control_service: RuntimeControlService | None = None,
        question_registry: Any | None = None,
    ) -> None:
        self.executions = executions
        self.activation = AgentActivationService(input_resolver)
        self.turn_runner = turn_runner or self._default_turn_runner
        self.control_service = control_service
        self.question_registry = question_registry
        self._handles: dict[tuple[str, str, int], AgentDriverHandle] = {}
        self._handles_lock = threading.RLock()
        self._finished: set[tuple[str, str, int]] = set()

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()

    async def activate(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput | None,
    ) -> DriverBinding[AgentDriverHandle]:
        if self.executions is None:
            raise AgentDriverError("store_required", "Agent activation requires an execution store")
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise AgentDriverError("execution_not_found", f"execution not found: {attempt.execution_id}")
        if (
            attempt.status is not AttemptStatus.ACTIVE
            or execution.status is not ExecutionStatus.RUNNING
            or execution.current_attempt_id != attempt.attempt_id
            or execution.owner_lease.get("generation") != attempt.generation
        ):
            raise AgentDriverError(
                "stale_attempt",
                "Agent activation does not match the durable execution owner",
            )
        record = self.executions.get_execution_input(attempt.execution_id)
        if record is None:
            raise AgentDriverError(
                "input_not_found",
                f"immutable Agent input is missing for {attempt.execution_id}",
            )
        if record.session_id != execution.session_id:
            raise AgentDriverError(
                "input_session_mismatch",
                "immutable Agent input does not match the execution session",
            )
        request = self.activation.build_request(record, activation)
        cancel_event = threading.Event()
        task = asyncio.create_task(
            self._run_attempt(attempt, request, cancel_event),
            name=f"openprogram-agent-{attempt.execution_id}-{attempt.generation}",
        )
        handle = AgentDriverHandle(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            session_id=record.session_id,
            cancel_event=cancel_event,
            done=task,
        )
        key = self._key(handle)
        with self._handles_lock:
            if any(existing.execution_id == handle.execution_id for existing in self._handles.values()):
                task.cancel()
                raise AgentDriverError("owner_exists", "execution already has a live Agent owner")
            self._handles[key] = handle
        task.add_done_callback(lambda _task: self._release(handle))
        # Returning the binding lets RuntimeControlService register this
        # exact owner atomically in its live-handle registry. The binding is
        # also the only object accepted by the registry for later dispatch.
        return DriverBinding(
            execution_id=handle.execution_id,
            attempt_id=handle.attempt_id,
            generation=handle.generation,
            driver=self,
            handle=handle,
        )

    async def request_pause(
        self, handle: AgentDriverHandle, command_id: str
    ) -> DriverAck:
        del handle, command_id
        raise AgentDriverError("unsupported", "Agent driver has no pause capability")

    async def request_cancel(
        self, handle: AgentDriverHandle, command_id: str
    ) -> DriverAck:
        self._require_live(handle)
        handle.cancel_event.set()
        registry = self.question_registry
        if registry is None:
            from openprogram.agent.questions import get_question_registry

            registry = get_question_registry()
        registry.cancel_execution(handle.session_id, handle.execution_id)
        return DriverAck(command_id=command_id, attempt_id=handle.attempt_id)

    async def inspect(self, handle: AgentDriverHandle) -> RuntimeSnapshot:
        self._require_live(handle)
        return RuntimeSnapshot(
            attempt_id=handle.attempt_id,
            state_schema_version=0,
            safe_point_kind=None,
            state={"done": handle.done.done()},
        )

    async def terminate(
        self, handle: AgentDriverHandle, reason: str
    ) -> TerminationReceipt:
        self._require_live(handle)
        handle.cancel_event.set()
        registry = self.question_registry
        if registry is None:
            from openprogram.agent.questions import get_question_registry

            registry = get_question_registry()
        registry.cancel_execution(handle.session_id, handle.execution_id)
        return TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=handle.done.done(),
            reason=reason,
        )

    async def _run_attempt(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
    ) -> Any:
        try:
            result = await asyncio.to_thread(
                self._run_turn,
                attempt,
                request,
                cancel_event,
            )
        except BaseException:
            if cancel_event.is_set():
                self._finish_attempt(attempt, None, cancel_event)
            else:
                self._recover_owner_loss(attempt)
            return None
        self._finish_attempt(attempt, result, cancel_event)
        return result

    def _run_turn(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
    ) -> Any:
        from openprogram.agent.run_control import (
            _current_token,
            claim_cancel_event,
            current_token,
            reset_current_execution_id,
            reset_current_session_id,
            set_current_execution_id,
            set_current_session_id,
            unregister_cancel_event,
        )

        if not claim_cancel_event(
            request.session_id,
            cancel_event,
            execution_id=attempt.execution_id,
        ):
            raise AgentDriverError("owner_conflict", "Agent execution already has a live runtime")
        session_token = set_current_session_id(request.session_id)
        execution_token = set_current_execution_id(attempt.execution_id)
        bound = current_token(request.session_id, execution_id=attempt.execution_id)
        token_token = _current_token.set(bound) if bound is not None else None
        try:
            result = self.turn_runner(request=request, cancel_event=cancel_event)
            if inspect.isawaitable(result):
                raise AgentDriverError("invalid_runner", "Agent turn runner must be synchronous")
            return result
        finally:
            if token_token is not None:
                _current_token.reset(token_token)
            reset_current_execution_id(execution_token)
            reset_current_session_id(session_token)
            unregister_cancel_event(
                request.session_id,
                cancel_event,
                execution_id=attempt.execution_id,
            )

    def _finish_attempt(
        self,
        attempt: AttemptRecord,
        result: Any,
        cancel_event: threading.Event,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        with self._handles_lock:
            if key in self._finished:
                return
            self._finished.add(key)
        service = self._control_service()
        execution = service.executions.get_execution(attempt.execution_id)
        if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
            return
        cancelled = cancel_event.is_set() or execution.status is ExecutionStatus.CANCELLING
        if cancelled and execution.status is not ExecutionStatus.CANCELLING:
            # terminate() is only a physical signal. Without a durable
            # cancelling intent the canonical service cannot legally move a
            # running execution directly to cancelled; owner recovery is the
            # safe terminal path.
            self._recover_owner_loss(attempt)
            return
        failed = bool(getattr(result, "failed", False))
        target = (
            ExecutionStatus.CANCELLED
            if cancelled
            else ExecutionStatus.FAILED
            if failed
            else ExecutionStatus.COMPLETED
        )
        outcome = "cancelled" if cancelled else "failed" if failed else "completed"
        try:
            service.finish_attempt(
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=execution.status_version,
                target=target,
                outcome=outcome,
                reason_code=("cancelled" if cancelled else None),
            )
        except Exception:
            # The canonical service owns the retry/recovery decision. A stale
            # completion must never write a terminal state directly.
            return

    def _recover_owner_loss(self, attempt: AttemptRecord) -> None:
        try:
            self._control_service().recover_owner_loss(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        except Exception:
            return

    def _control_service(self) -> RuntimeControlService:
        if self.control_service is None:
            if self.executions is None:
                raise AgentDriverError("store_required", "execution store is required")
            self.control_service = RuntimeControlService(
                self.executions,
                AttemptStore(self.executions),
                registry=self._new_registry(),
            )
        return self.control_service

    @staticmethod
    def _new_registry():
        from openprogram.execution.driver import DriverRegistry

        return DriverRegistry()

    def _execution_session(self, execution_id: str) -> str:
        assert self.executions is not None
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            raise AgentDriverError("execution_not_found", f"execution not found: {execution_id}")
        return execution.session_id

    def _require_live(self, handle: AgentDriverHandle) -> None:
        if not isinstance(handle, AgentDriverHandle):
            raise AgentDriverError("invalid_handle", "Agent driver handle is invalid")
        key = self._key(handle)
        with self._handles_lock:
            current = self._handles.get(key)
        if current is not handle:
            raise AgentDriverError("stale_handle", "Agent driver handle is no longer live")

    def _release(self, handle: AgentDriverHandle) -> None:
        key = self._key(handle)
        with self._handles_lock:
            if self._handles.get(key) is handle:
                self._handles.pop(key, None)

    @staticmethod
    def _key(handle: AgentDriverHandle) -> tuple[str, str, int]:
        return handle.execution_id, handle.attempt_id, handle.generation

    @staticmethod
    def _default_turn_runner(*, request: Any, cancel_event: threading.Event) -> Any:
        from openprogram.agent.dispatcher import process_user_turn

        return process_user_turn(request, cancel_event=cancel_event)


__all__ = [
    "AgentActivationService",
    "AgentDriverError",
    "AgentDriverHandle",
    "AgentProductionDriver",
]
