"""AgentProductionDriver service operations."""
from __future__ import annotations
import inspect
from openprogram.runtime.conversation.production_driver import shared
from openprogram.runtime.conversation.production_driver.activation import ActivationOperations
from openprogram.runtime.conversation.production_driver.execution import ExecutionOperations
from openprogram.runtime.conversation.production_driver.safe_points import SafePointsOperations
from openprogram.runtime.conversation.production_driver.finalization import FinalizationOperations
from openprogram.runtime.execution.control.service import RuntimeControlService
from openprogram.runtime.execution.attempts import AttemptRecord
from openprogram.runtime.execution.store.store import ExecutionStore
from openprogram.runtime.execution.model import ExecutionStatus
from openprogram.runtime.execution.driver import ActivationInput


class AgentProductionDriver(ActivationOperations, ExecutionOperations, SafePointsOperations, FinalizationOperations):
    """Internal Agent execution driver with exact owner fencing.

    Ordinary chat advertises provider-decision and tool-action safe points.
    Cancellation remains a cooperative signal; forced-tool and nonordinary
    entries retain their narrower capability contract.
    """


    def __init__(
        self,
        executions: ExecutionStore | None,
        *,
        input_resolver: shared.InputResolver | None = None,
        turn_runner: shared.TurnRunner | None = None,
        control_service: RuntimeControlService | None = None,
        question_registry: shared.Any | None = None,
        event_sink: shared.Callable[[dict], None] | None = None,
        activation_observer: shared.Callable[[ActivationInput], None] | None = None,
        job_resume_resolver: shared.Callable[[str], str | None] | None = None,
        run_ended: shared.Callable[[ExecutionStore, shared.Any, shared.Any], None] | None = None,
    ) -> None:
        self.executions = executions
        self.activation = shared.AgentActivationService(
            input_resolver or self._resolve_durable_input
        )
        self.turn_runner = turn_runner or self._default_turn_runner
        self.control_service = control_service
        self.question_registry = question_registry
        self.event_sink = event_sink
        self.activation_observer = activation_observer
        self.job_resume_resolver = job_resume_resolver
        self.run_ended = run_ended
        self._handles: dict[tuple[str, str, int], shared.AgentDriverHandle] = {}
        self._handles_lock = shared.threading.RLock()
        self._continuation_start_gates: dict[tuple[str, str, int], shared.threading.Event] = {}
        self._continuation_committed: set[tuple[str, str, int]] = set()
        self._finished: set[tuple[str, str, int]] = set()
        self._cancel_commands: dict[tuple[str, str, int], str] = {}


    @staticmethod
    def _new_registry():
        from openprogram.runtime.execution.driver import DriverRegistry

        return DriverRegistry()


    def _execution_session(self, execution_id: str) -> str:
        assert self.executions is not None
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            raise shared.AgentDriverError("execution_not_found", f"execution not found: {execution_id}")
        return execution.session_id


    def _require_live(self, handle: shared.AgentDriverHandle) -> None:
        if not isinstance(handle, shared.AgentDriverHandle):
            raise shared.AgentDriverError("invalid_handle", "Agent driver handle is invalid")
        key = self._key(handle)
        with self._handles_lock:
            current = self._handles.get(key)
        if current is not handle:
            raise shared.AgentDriverError("stale_handle", "Agent driver handle is no longer live")


    def _release(self, handle: shared.AgentDriverHandle) -> None:
        try:
            self._control_service().attempts.set_process_owner(
                handle.attempt_id, generation=handle.generation, active=False,
            )
        except Exception:
            # Local handle cleanup must still happen.
            shared._log.exception("failed to release Agent process owner %s", handle.attempt_id)
        key = self._key(handle)
        with self._handles_lock:
            if self._handles.get(key) is handle:
                self._handles.pop(key, None)
            self._continuation_start_gates.pop(key, None)
            self._continuation_committed.discard(key)
            self._finished.discard(key)
            self._cancel_commands.pop(key, None)


    @staticmethod
    def _key(handle: shared.AgentDriverHandle) -> tuple[str, str, int]:
        return handle.execution_id, handle.attempt_id, handle.generation


    @staticmethod
    def _default_turn_runner(
        *, request: shared.Any, cancel_event: shared.threading.Event,
        on_event: shared.Callable[[dict], None] | None = None,
        execution_context: shared.Mapping[str, shared.Any] | None = None,
    ) -> shared.Any:
        from openprogram.runtime.conversation.sub_agent_run import (
            validate_self_update_turn_request,
        )
        from openprogram.runtime.conversation.turn_runtime import process_user_turn

        validate_self_update_turn_request(request)
        kwargs = {}
        try:
            params = inspect.signature(process_user_turn).parameters
            if "on_event" in params:
                kwargs["on_event"] = on_event
            if "cancel_event" in params:
                kwargs["cancel_event"] = cancel_event
            if "execution_context" in params:
                kwargs["execution_context"] = execution_context
        except (TypeError, ValueError):
            kwargs = {"on_event": on_event, "cancel_event": cancel_event, "execution_context": execution_context}
        return process_user_turn(request, **kwargs)

