"""Internal production driver for canonical Agent executions.

This module provides the Agent activation boundary: an immutable, versioned
admission input is resolved into the existing dispatcher request, a live owner
is bound to one attempt generation, and completion is written through the
canonical control service only.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import asdict, dataclass, fields, is_dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from openprogram.execution.attempts import (
    AttemptConflict,
    AttemptRecord,
    AttemptStatus,
    AttemptStore,
)
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import (
    ActivationInput,
    DriverAck,
    DriverBinding,
    RuntimeSnapshot,
    TerminationReceipt,
)
from openprogram.execution.model import (
    CapabilitySet,
    CommandKind,
    CommandStatus,
    ExecutionStatus,
    TERMINAL_EXECUTION_STATUSES,
)
from openprogram.execution.store import ExecutionStore
from openprogram.agent.continuation import (
    AGENT_CHECKPOINT_SCHEMA_VERSION,
    MAX_AGENT_CHECKPOINT_BYTES,
    MAX_AGENT_DELTA_BYTES,
    MAX_AGENT_PENDING_MESSAGES,
    MAX_AGENT_REPEAT_FAILURES,
    MAX_AGENT_STATE_BLOB_BYTES,
    MAX_AGENT_STATE_REFS,
    MAX_AGENT_TERMINAL_EFFECT_RECEIPTS,
    AgentCheckpointError,
    AgentCheckpointV1,
    AgentContinuation,
    canonical_json_bytes,
    validate_runtime_contract,
)


_log = logging.getLogger(__name__)


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
    done: Any


class _ThreadResultFuture(Future[Any]):
    """A result future that remains awaitable from an activation loop.

    Continue/step dispatch can be called by a short-lived WebSocket event
    loop.  A continuation producer must outlive that command handler, so it
    cannot be owned by that loop's ``asyncio.create_task``.
    """

    def __await__(self):
        return asyncio.wrap_future(self).__await__()


@dataclass(frozen=True)
class CanonicalAgentAdmission:
    """Durably admitted Agent turn before its attempt is activated."""

    execution_id: str
    session_id: str
    status_version: int


@dataclass(frozen=True)
class CanonicalAgentActivation:
    admission: CanonicalAgentAdmission
    attempt_id: str
    generation: int
    status_version: int


InputResolver = Callable[[Any], Mapping[str, Any]]
TurnRunner = Callable[..., Any]

AGENT_TURN_INPUT_VERSION = 1
MAX_AGENT_TURN_INPUT_BYTES = 256 * 1024
AGENT_SAFE_POINT_KINDS = (
    "agent.provider.decision.after",
    "agent.tool.action.after",
    "agent.wait.before_tool",
)
FINISH_RETRY_LIMIT = 8
FINISH_RETRY_MAX_DELAY = 1.0
FINISH_REPAIR_RETRY_TIMER_DELAY = 30.0
_PAYLOAD_KINDS = frozenset({"chat", "forced_tool"})
_PAYLOAD_ENVELOPE_KEYS = frozenset({"version", "kind", "request", "tool_name", "tool_input", "anchor_msg_id", "work_dir", "agent_id", "source", "provider", "model", "response_format", "surface_context_snapshot"})


def _json_payload(value: Any) -> str:
    try:
        return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AgentDriverError("invalid_input", "Agent admission input must be JSON serializable") from exc


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def normalize_agent_turn_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the bounded, versioned durable Agent input envelope."""
    if not isinstance(payload, Mapping):
        raise AgentDriverError("invalid_input", "Agent admission input must be an object")
    value = copy.deepcopy(dict(payload))
    if value.get("version") != AGENT_TURN_INPUT_VERSION:
        raise AgentDriverError("invalid_input_version", "unsupported Agent admission input version")
    kind = value.get("kind")
    if kind not in _PAYLOAD_KINDS:
        raise AgentDriverError("invalid_input_kind", "Agent admission input kind must be chat or forced_tool")
    if set(value) - _PAYLOAD_ENVELOPE_KEYS:
        raise AgentDriverError("invalid_input", "Agent admission input has unknown fields")
    if kind == "chat":
        request = value.get("request")
        if not isinstance(request, Mapping):
            raise AgentDriverError("invalid_input", "chat input requires a request object")
        request = _json_safe(copy.deepcopy(dict(request)))
        from openprogram.agent.dispatcher.types import TurnRequest

        request_fields = frozenset(field.name for field in fields(TurnRequest))
        if set(request) - request_fields:
            raise AgentDriverError("invalid_input", "chat input has unknown request fields")
        for required in ("user_text", "agent_id", "source"):
            if not isinstance(request.get(required), str) or not request[required]:
                raise AgentDriverError(
                    "invalid_input", f"chat input requires {required}"
                )
        value = {"version": AGENT_TURN_INPUT_VERSION, "kind": kind, "request": request}
    else:
        allowed = {"version", "kind", "tool_name", "tool_input", "anchor_msg_id", "work_dir", "agent_id", "source", "provider", "model", "response_format", "surface_context_snapshot"}
        if set(value) - allowed:
            raise AgentDriverError("invalid_input", "forced_tool input has unknown fields")
        if not isinstance(value.get("tool_name"), str) or not value["tool_name"]:
            raise AgentDriverError("invalid_input", "forced_tool input requires tool_name")
        if not isinstance(value.get("tool_input", {}), Mapping):
            raise AgentDriverError("invalid_input", "forced_tool input requires an object tool_input")
        value["tool_input"] = _json_safe(copy.deepcopy(dict(value["tool_input"])))
    value = _json_safe(value)
    encoded = _json_payload(value)
    if len(encoded.encode("utf-8")) > MAX_AGENT_TURN_INPUT_BYTES:
        raise AgentDriverError("input_too_large", "Agent admission input exceeds the size limit")
    return value


@dataclass(frozen=True)
class ForcedToolActivation:
    session_id: str
    tool_name: str
    tool_input: Mapping[str, Any]
    anchor_msg_id: str = ""
    work_dir: str | None = None
    agent_id: str = "main"
    source: str = "web"
    provider: str | None = None
    model: str | None = None
    response_format: Any = None
    surface_context_snapshot: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class JobAgentActivation:
    request: Any
    job_context: Mapping[str, Any]


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
        payload = self._input_resolver(record)
        if not isinstance(payload, Mapping):
            raise AgentDriverError(
                "invalid_input",
                "Agent admission input must resolve to an object",
            )
        # The resolver is an external durable-input boundary. Copy the full
        # payload before constructing the mutable TurnRequest so later changes
        # to a cache or transport object cannot alter the admitted turn.
        from openprogram.agent.dispatcher.types import TurnRequest

        envelope = normalize_agent_turn_payload(payload)
        if envelope["kind"] != "chat":
            raise AgentDriverError(
                "wrong_input_kind",
                "forced_tool input must be activated by the forced-tool runner",
            )
        values = envelope["request"]

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
        if isinstance(values.get("permission_rules"), Mapping):
            from openprogram.agent.session_config import _as_permission_rules

            values["permission_rules"] = _as_permission_rules(
                values["permission_rules"]
            )
        if isinstance(values.get("response_format"), Mapping):
            try:
                from openprogram.providers.structured_output import normalize_response_format

                values["response_format"] = normalize_response_format(
                    values["response_format"]
                )
            except Exception:
                pass
        return TurnRequest(session_id=record.session_id, **values)

    def build_job_activation(self, record: Any) -> JobAgentActivation:
        from openprogram.agent.job.input import JobAgentInputError, JobAgentInputV1

        payload = self._input_resolver(record)
        if not isinstance(payload, Mapping):
            raise AgentDriverError("invalid_input", "Job Agent input must resolve to an object")
        try:
            value = JobAgentInputV1.parse(payload)
            request = value.to_turn_request(session_id=record.session_id)
        except JobAgentInputError as exc:
            raise AgentDriverError("invalid_job_input", str(exc)) from exc
        return JobAgentActivation(request, copy.deepcopy(dict(value.job_context)))


class AgentProductionDriver:
    """Internal Agent execution driver with exact owner fencing.

    Ordinary chat advertises provider-decision and tool-action safe points.
    Cancellation remains a cooperative signal; forced-tool and nonordinary
    entries retain their narrower capability contract.
    """

    def __init__(
        self,
        executions: ExecutionStore | None,
        *,
        input_resolver: InputResolver | None = None,
        turn_runner: TurnRunner | None = None,
        control_service: RuntimeControlService | None = None,
        question_registry: Any | None = None,
        event_sink: Callable[[dict], None] | None = None,
        activation_observer: Callable[[ActivationInput], None] | None = None,
        job_resume_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.executions = executions
        self.activation = AgentActivationService(
            input_resolver or self._resolve_durable_input
        )
        self.turn_runner = turn_runner or self._default_turn_runner
        self.control_service = control_service
        self.question_registry = question_registry
        self.event_sink = event_sink
        self.activation_observer = activation_observer
        self.job_resume_resolver = job_resume_resolver
        self._handles: dict[tuple[str, str, int], AgentDriverHandle] = {}
        self._handles_lock = threading.RLock()
        self._continuation_start_gates: dict[tuple[str, str, int], threading.Event] = {}
        self._continuation_committed: set[tuple[str, str, int]] = set()
        self._finished: set[tuple[str, str, int]] = set()
        # Completion is an in-process durable outbox: releasing a driver
        # handle must not discard a terminal write that failed transiently.
        self._pending_finishes: dict[tuple[str, str, int], tuple[AttemptRecord, int, ExecutionStatus, str, str | None, str | None]] = {}
        self._finish_retry_worker_active = False
        self._finish_retry_timer: threading.Timer | None = None
        self._cancel_commands: dict[tuple[str, str, int], str] = {}
        self._finish_repair_stalled: set[tuple[str, str, int]] = set()
        self._finish_repair_metrics = {
            "persisted": 0,
            "backpressure": 0,
            "write_errors": 0,
            "stalled": 0,
        }

    def _resolve_durable_input(self, record: Any) -> Mapping[str, Any]:
        if self.executions is None:
            raise AgentDriverError("store_required", "Agent activation requires an execution store")
        payload = self.executions.get_agent_turn_input(record.execution_id)
        if payload is None:
            payload = self.executions.get_job_agent_input(record.execution_id)
        if payload is None:
            raise AgentDriverError(
                "input_not_found",
                f"durable Agent turn input is missing for {record.execution_id}",
            )
        return payload

    def _resolve_activation_input(
        self, record: Any, activation: ActivationInput | None,
    ) -> Any:
        payload = self.activation._input_resolver(record)
        if not isinstance(payload, Mapping):
            raise AgentDriverError("invalid_input", "Agent admission input must resolve to an object")
        if payload.get("kind") == "job_agent":
            resolved = self.activation.build_job_activation(record)
            if self.job_resume_resolver is not None:
                resume_parent = self.job_resume_resolver(record.execution_id)
                if resume_parent is not None:
                    resolved.request.branch_from = resume_parent
            setattr(resolved.request, "_job_context", resolved.job_context)
            return resolved.request
        envelope = normalize_agent_turn_payload(payload)
        if envelope["kind"] == "chat":
            return self.activation.build_request(record, activation)
        if activation is not None and (activation.checkpoint is not None or activation.steer_inputs):
            raise AgentDriverError(
                "unsupported_activation_state",
                "forced-tool activations do not support Agent checkpoints",
            )
        return ForcedToolActivation(
            session_id=record.session_id,
            tool_name=envelope["tool_name"],
            tool_input=envelope["tool_input"],
            anchor_msg_id=str(envelope.get("anchor_msg_id") or ""),
            work_dir=envelope.get("work_dir"),
            agent_id=str(envelope.get("agent_id") or "main"),
            source=str(envelope.get("source") or "web"),
            provider=envelope.get("provider"),
            model=envelope.get("model"),
            response_format=envelope.get("response_format"),
            surface_context_snapshot=envelope.get("surface_context_snapshot"),
        )

    def resolve_existing_job(self, execution_id: str) -> JobAgentActivation:
        """Resolve existing immutable Job input without admitting an execution."""
        if self.executions is None:
            raise AgentDriverError("store_required", "Job activation requires an execution store")
        record = self.executions.get_execution_input(execution_id)
        if record is None:
            raise AgentDriverError("input_not_found", f"immutable Job input is missing for {execution_id}")
        if self.executions.get_job_agent_input(execution_id) is None:
            raise AgentDriverError("wrong_input_kind", f"execution {execution_id} is not a Job Agent input")
        return self.activation.build_job_activation(record)

    @staticmethod
    def capabilities_for_payload(payload: Mapping[str, Any]) -> CapabilitySet:
        """Return the admitted capability contract, never a transport guess."""
        if not isinstance(payload, Mapping):
            raise AgentDriverError("invalid_input", "Agent admission input must be an object")
        if payload.get("kind") == "job_agent":
            # Job input has its own strict envelope.  A Job is still driven by
            # the same Agent loop, so it exposes the same provider/tool safe
            # points and durable steering contract as ordinary chat.
            from openprogram.agent.job.input import JobAgentInputError, JobAgentInputV1

            try:
                JobAgentInputV1.parse(payload)
            except JobAgentInputError as exc:
                raise AgentDriverError("invalid_job_input", str(exc)) from exc
            return CapabilitySet(
                pause=True,
                step=True,
                steer=True,
                fork=True,
                retry=True,
                safe_point_kinds=AGENT_SAFE_POINT_KINDS,
                state_schema_version=AGENT_CHECKPOINT_SCHEMA_VERSION,
            )
        envelope = normalize_agent_turn_payload(payload)
        if envelope["kind"] != "chat":
            return CapabilitySet()
        request = envelope["request"]
        text = str(request.get("user_text") or "").lstrip()
        if (
            request.get("interaction") in {"spawn", "merge"}
            or text.startswith(("/forced_tool", "/spawn", "/merge"))
        ):
            return CapabilitySet()
        return CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=AGENT_SAFE_POINT_KINDS,
            state_schema_version=AGENT_CHECKPOINT_SCHEMA_VERSION,
        )

    def capabilities(self) -> CapabilitySet:
        """Default driver capability used only by direct internal callers.

        Admission uses ``capabilities_for_payload`` so a forced tool cannot
        inherit ordinary-chat controls from a mutable driver instance.
        """
        return CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=AGENT_SAFE_POINT_KINDS,
            state_schema_version=AGENT_CHECKPOINT_SCHEMA_VERSION,
        )

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
        request = self._resolve_activation_input(record, activation)
        setattr(request, "_execution_revision_id", execution.revision_id)
        steer_inputs = tuple(activation.steer_inputs) if activation is not None else ()
        continuation = None
        if activation is not None and activation.checkpoint is not None:
            if isinstance(request, ForcedToolActivation):
                raise AgentDriverError(
                    "unsupported_activation_state",
                    "forced-tool activations do not support Agent checkpoints",
                )
            try:
                continuation = AgentContinuation.from_checkpoint(
                    store=self.executions,
                    checkpoint=activation.checkpoint,
                    request=request,
                )
                if (
                    record.user_message_id != continuation.state.payload["turn"]["user_message_id"]
                    or record.assistant_message_id != continuation.assistant_message_id
                ):
                    raise AgentDriverError(
                        "checkpoint_schema_invalid",
                        "Agent checkpoint branch anchors differ from immutable admission input",
                    )
                from openprogram.agent.dispatcher.loop_runner import resolve_agent_runtime
                from openprogram.agent.internals._workdir import runtime_location_for
                from openprogram.worktree.context import reset_worktree, set_worktree
                _location = runtime_location_for(request.session_id, use_context=False)
                _workdir_token = set_worktree(_location["workdir"])
                try:
                    _profile, _tools, _recordable, _prompt, _model, _contract = resolve_agent_runtime(
                        request,
                        assistant_msg_id=continuation.assistant_message_id,
                    )
                finally:
                    reset_worktree(_workdir_token)
                validate_runtime_contract(continuation.resolved_snapshot, _contract)
            except AgentCheckpointError as exc:
                raise AgentDriverError(exc.code, str(exc)) from exc
        if activation is not None and activation.checkpoint is not None and self.activation_observer is not None:
            self.activation_observer(activation)
        cancel_event = threading.Event()
        # Activation is often initiated by a short-lived transport loop.  An
        # execution owner must not inherit that loop's cancellation lifetime,
        # including the initial Job attempt.  The driver owns a thread-backed
        # completion future for both initial and resumed activations.
        task: Any = _ThreadResultFuture()
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
                if hasattr(task, "cancel"):
                    task.cancel()
                raise AgentDriverError("owner_exists", "execution already has a live Agent owner")
            self._handles[key] = handle
            self._continuation_start_gates[key] = threading.Event()
        task.add_done_callback(lambda _task: self._release(handle))
        def _run_owned_attempt() -> None:
            with self._handles_lock:
                gate = self._continuation_start_gates.get(key)
            if gate is None:
                task.cancel()
                return
            gate.wait()
            with self._handles_lock:
                if key not in self._continuation_committed:
                    task.cancel()
                    return
            try:
                result = asyncio.run(
                    self._run_attempt(
                        attempt, request, cancel_event,
                        continuation=continuation,
                        steer_inputs=steer_inputs,
                    )
                )
            except BaseException as exc:
                task.set_exception(exc)
            else:
                task.set_result(result)

        threading.Thread(
            target=_run_owned_attempt,
            daemon=True,
            name=f"openprogram-agent-{attempt.execution_id}-{attempt.generation}",
        ).start()
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

    def activation_committed(self, binding: DriverBinding[AgentDriverHandle]) -> None:
        """Release a driver-owned producer after registry fencing commits."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.get(key)
            if gate is None:
                return
            self._continuation_committed.add(key)
            gate.set()

    def activation_aborted(self, binding: DriverBinding[AgentDriverHandle]) -> None:
        """Discard a producer whose registry bind lost a fence."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.pop(key, None)
            self._continuation_committed.discard(key)
        if gate is not None:
            gate.set()

    async def request_pause(
        self, handle: AgentDriverHandle, command_id: str
    ) -> DriverAck:
        self._require_live(handle)
        # Pause is cooperative: the loop observes the durable APPLYING
        # command at its next declared provider/tool boundary.  ACK only
        # confirms delivery to this fenced owner; it never fabricates a
        # checkpoint or changes command state.
        return DriverAck(command_id=command_id, attempt_id=handle.attempt_id)

    async def request_cancel(
        self, handle: AgentDriverHandle, command_id: str
    ) -> DriverAck:
        self._require_live(handle)
        # The canonical control service has already fenced and dispatched this
        # exact command_id to this owner. Retain it even if a diagnostic read
        # of the command row is temporarily unavailable; finish/repair must
        # carry the same identity to the durable command transition.
        with self._handles_lock:
            self._cancel_commands[self._key(handle)] = command_id
        handle.cancel_event.set()
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
        killed = False
        try:
            from openprogram.agent.process_runner import kill_active_subprocess

            killed = kill_active_subprocess(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            _log.exception(
                "failed to terminate Agent subprocess for %s",
                handle.execution_id,
            )
        try:
            from openprogram.agent.run_control import kill_active_runtime

            kill_active_runtime(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            _log.exception(
                "failed to terminate Agent runtime for %s",
                handle.execution_id,
            )
        return TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=killed or handle.done.done(),
            reason=reason,
        )

    def fail_admission(
        self, admission: CanonicalAgentAdmission, *, reason_code: str,
        target: ExecutionStatus = ExecutionStatus.FAILED,
    ) -> None:
        """Finish an admitted turn that could not create a live owner.

        Thread/process startup failures happen before ``activate`` can bind a
        handle. This driver-owned path still leases the exact attempt and
        records the failure through Control Service, without allowing a
        transport or DAG helper to write execution lifecycle state.
        """
        service = self._control_service()
        try:
            attempt, leased = service.attempts.lease(
                admission.execution_id,
                expected_version=admission.status_version,
                owner_id=f"agent-failure-{uuid.uuid4().hex}",
                ttl_seconds=30,
            )
            active, running = service.attempts.activate(
                attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=leased.status_version,
            )
            service.finish_attempt(
                attempt_id=active.attempt_id,
                generation=active.generation,
                expected_execution_version=running.status_version,
                target=target,
                outcome="cancelled" if target is ExecutionStatus.CANCELLED else "failed",
                reason_code=reason_code,
            )
        except Exception:
            # The durable record may already have been handled by another
            # owner or recovery pass. Never replace that state from a
            # transport startup exception.
            return

    async def _run_attempt(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
        *,
        continuation: AgentContinuation | None = None,
        steer_inputs: tuple[Mapping[str, Any], ...] = (),
    ) -> Any:
        try:
            result = await asyncio.to_thread(
                self._run_turn,
                attempt,
                request,
                cancel_event,
                continuation,
                steer_inputs,
            )
        except asyncio.CancelledError:
            if cancel_event.is_set():
                self._finish_attempt(attempt, None, cancel_event)
            else:
                self._recover_owner_loss(attempt)
            return None
        except Exception as exc:
            if cancel_event.is_set():
                self._finish_attempt(attempt, None, cancel_event)
            else:
                failure = type(
                    "RunnerFailure", (),
                    {"failed": True, "error": f"{type(exc).__name__}: {exc}"},
                )()
                self._finish_attempt(
                    attempt,
                    failure,
                    cancel_event,
                    failure_reason="agent_runner_error",
                )
                return failure
            return None
        except BaseException:
            # Process-level termination and other non-Exception failures do
            # not provide a trustworthy runner outcome. Let canonical owner
            # recovery decide the terminal state under the exact owner fence.
            self._recover_owner_loss(attempt)
            return None
        self._finish_attempt(attempt, result, cancel_event)
        return result

    def _run_turn(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
        continuation: AgentContinuation | None = None,
        steer_inputs: tuple[Mapping[str, Any], ...] = (),
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
        setattr(request, "_execution_revision_id", attempt.execution_id)
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is not None:
            setattr(request, "_execution_revision_id", execution.revision_id)
        session_token = set_current_session_id(request.session_id)
        execution_token = set_current_execution_id(attempt.execution_id)
        bound = current_token(request.session_id, execution_id=attempt.execution_id)
        token_token = _current_token.set(bound) if bound is not None else None
        job_tokens: list[Any] = []
        worktree_token = None
        try:
            job_context = getattr(request, "_job_context", None)
            if isinstance(job_context, Mapping):
                from openprogram.agent.job.runner import (
                    _current_job_governance,
                    _current_job_id,
                    _current_job_runner,
                    runner_for_execution_store,
                )

                job_runner = runner_for_execution_store(self.executions)
                if job_runner is None:
                    raise AgentDriverError(
                        "job_runner_unavailable",
                        "canonical Job owner has no resource runner",
                    )
                job = job_runner._canonical_job(attempt.execution_id)
                if job is None:
                    raise AgentDriverError(
                        "job_projection_missing",
                        "canonical Job projection is unavailable",
                    )
                job_tokens.extend((
                    _current_job_id.set(attempt.execution_id),
                    _current_job_runner.set(job_runner),
                    _current_job_governance.set(
                        job_runner._governance_context(job),
                    ),
                ))
                chain = job_context.get("chain")
                if isinstance(chain, Mapping):
                    from openprogram.programs.tools.agents.send_message.send_message.depth import (
                        set_chain_generations,
                        set_chain_messages,
                    )

                    job_tokens.extend((
                        set_chain_messages(int(chain.get("messages") or 0)),
                        set_chain_generations(int(chain.get("generations") or 0)),
                    ))
                worktree_id = job_context.get("worktree_id")
                if isinstance(worktree_id, str) and worktree_id:
                    from openprogram.worktree.context import set_worktree
                    from openprogram.worktree.manager import get_manager

                    worktree = get_manager().get_worktree(worktree_id)
                    if worktree is None:
                        raise AgentDriverError(
                            "worktree_not_found",
                            f"Job worktree is unavailable: {worktree_id}",
                        )
                    worktree_token = set_worktree(worktree.worktree_path)
            steer_queue = [copy.deepcopy(dict(item)) for item in steer_inputs]
            steer_consumed_ids: set[str] = set()
            if continuation is not None:
                from openprogram.agent.dispatcher import process_agent_continuation

                execution_context = {
                    "safe_point_hook": self._safe_point_hook(
                        attempt, request, cancel_event,
                        continuation=continuation,
                        steer_queue=steer_queue,
                        steer_consumed_ids=steer_consumed_ids,
                    ),
                    "canonical_execution": True,
                    "steer_inputs": steer_queue,
                    "steer_consumed_ids": steer_consumed_ids,
                }
                if getattr(request, "_job_context", None) is not None:
                    execution_context["job_context"] = copy.deepcopy(request._job_context)
                result = process_agent_continuation(
                    continuation,
                    on_event=self.event_sink,
                    cancel_event=cancel_event,
                    execution_context=execution_context,
                )
            elif isinstance(request, ForcedToolActivation):
                from openprogram.agent.dispatcher import dispatch_forced_tool_call

                result = dispatch_forced_tool_call(
                    session_id=request.session_id,
                    anchor_msg_id=request.anchor_msg_id,
                    tool_name=request.tool_name,
                    tool_input=dict(request.tool_input),
                    work_dir=request.work_dir,
                    agent_id=request.agent_id,
                    source=request.source,
                    provider=request.provider,
                    model=request.model,
                    response_format=request.response_format,
                    on_event=self.event_sink,
                    execution_id=attempt.execution_id,
                    attempt_id=attempt.attempt_id,
                    generation=attempt.generation,
                    cancel_event=cancel_event,
                    surface_context_snapshot=(
                        dict(request.surface_context_snapshot)
                        if request.surface_context_snapshot is not None else None
                    ),
                )
            else:
                runner_kwargs = {
                    "request": request,
                    "cancel_event": cancel_event,
                }
                try:
                    parameters = inspect.signature(self.turn_runner).parameters
                    if "on_event" in parameters:
                        runner_kwargs["on_event"] = self.event_sink
                    if "execution_context" in parameters:
                        runner_kwargs["execution_context"] = {
                            "safe_point_hook": self._safe_point_hook(
                                attempt, request, cancel_event,
                                steer_queue=steer_queue,
                                steer_consumed_ids=steer_consumed_ids,
                            ),
                            "canonical_execution": True,
                            "steer_inputs": steer_queue,
                            "steer_consumed_ids": steer_consumed_ids,
                        }
                        if getattr(request, "_job_context", None) is not None:
                            runner_kwargs["execution_context"]["job_context"] = copy.deepcopy(request._job_context)
                except (TypeError, ValueError):
                    pass
                result = self.turn_runner(**runner_kwargs)
            if inspect.isawaitable(result):
                raise AgentDriverError("invalid_runner", "Agent turn runner must be synchronous")
            return result
        finally:
            if worktree_token is not None:
                from openprogram.worktree.context import reset_worktree

                reset_worktree(worktree_token)
            for context_token in reversed(job_tokens):
                context_token.var.reset(context_token)
            if token_token is not None:
                _current_token.reset(token_token)
            reset_current_execution_id(execution_token)
            reset_current_session_id(session_token)
            from openprogram.agent.run_control import unregister_active_runtime

            unregister_active_runtime(
                request.session_id,
                execution_id=attempt.execution_id,
            )
            unregister_cancel_event(
                request.session_id,
                cancel_event,
                execution_id=attempt.execution_id,
            )

    def _safe_point_hook(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
        *,
        continuation: AgentContinuation | None = None,
        steer_queue: list[dict[str, Any]] | None = None,
        steer_consumed_ids: set[str] | None = None,
    ) -> Callable[[str, Mapping[str, Any]], bool]:
        """Bind Agent-loop boundaries to the canonical execution owner.

        The closure carries only durable identifiers and JSON payloads.  It
        deliberately retains no provider stream, coroutine, tool object, or
        dispatcher-local state for a future attempt.
        """
        from openprogram.execution.effects import (
            EffectClassification, EffectStatus,
        )
        from openprogram.execution.model import CommandKind

        pending: dict[
            str, tuple[str, str, str, str | None, tuple[dict[str, Any], ...]]
        ] = {}
        prior_actions: list[dict[str, Any]] = []
        prior_receipts: list[dict[str, Any]] = []
        completed_tool_results: list[dict[str, Any]] = []
        provider_action_id = ""
        provider_effect_id = ""
        provider_input_hash = ""
        provider_terminal_receipt: dict[str, Any] | None = None
        latest_assistant: dict[str, Any] | None = None
        latest_snapshot: dict[str, Any] = {}
        if continuation is not None:
            prior_actions = [dict(item) for item in continuation.state.payload["completed_actions"]]
            for item in continuation.state.payload["terminal_effect_receipts"]:
                receipt = dict(item)
                receipt_ref = receipt.pop("receipt_ref", None)
                if not isinstance(receipt_ref, Mapping):
                    raise AgentDriverError("checkpoint_schema_invalid", "terminal receipt ref is missing")
                try:
                    receipt["receipt"] = continuation.state.read_json_ref(
                        self.executions, attempt.execution_id, receipt_ref,
                    )
                except AgentCheckpointError as exc:
                    raise AgentDriverError(exc.code, str(exc)) from exc
                prior_receipts.append(receipt)
            completed_tool_results = [item.model_dump(mode="json") for item in continuation.tool_results]
            provider_action_id = continuation.provider_action_id
            latest_assistant = continuation.assistant_message.model_dump(mode="json")
            latest_snapshot = dict(continuation.resolved_snapshot)
            for action in prior_actions:
                if action.get("action_id") == provider_action_id:
                    candidate_hash = action.get("input_hash")
                    if isinstance(candidate_hash, str):
                        provider_input_hash = candidate_hash
                    break
            for receipt in prior_receipts:
                if receipt.get("action_id") == provider_action_id:
                    candidate_effect = receipt.get("effect_id")
                    candidate_receipt = receipt.get("receipt")
                    if isinstance(candidate_effect, str):
                        provider_effect_id = candidate_effect
                    if isinstance(candidate_receipt, Mapping):
                        provider_terminal_receipt = dict(candidate_receipt)
                    break

        def digest(*parts: str) -> str:
            value = "\x1f".join(parts).encode("utf-8")
            return hashlib.sha256(value).hexdigest()

        def json_digest(value: Any) -> str:
            return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

        def current_command(service, execution_id: str):
            commands = service.executions.list_commands(execution_id)
            priority = (CommandKind.PAUSE, CommandKind.STEP, CommandKind.STEER)
            for kind in priority:
                for command in commands:
                    if (
                        command.kind is kind
                        and command.status in {
                            CommandStatus.ACCEPTED,
                            CommandStatus.APPLYING,
                        }
                    ):
                        return command
            return None

        def checkpoint_payload(
            kind: str,
            payload: Mapping[str, Any],
            effect_id: str | None = None,
            action_id: str | None = None,
            input_hash: str | None = None,
            terminal_receipt: Mapping[str, Any] | None = None,
        ) -> AgentCheckpointV1:
            phase = "after_provider" if kind in {"provider.after", "wait.before_tool"} else "after_tool"
            point_kind = (
                "agent.wait.before_tool" if kind == "wait.before_tool" else
                "agent.provider.decision.after" if phase == "after_provider"
                else "agent.tool.action.after"
            )
            execution = self._control_service().executions.get_execution(
                attempt.execution_id
            )
            if execution is None:
                raise AgentDriverError("execution_not_found", "execution disappeared at safe point")
            raw_turn = payload.get("turn") if isinstance(payload.get("turn"), Mapping) else {}
            user_message_id = str(
                raw_turn.get("user_message_id")
                or getattr(execution, "user_message_id", None)
                or getattr(request, "user_msg_id", "")
                or "durable-user"
            )
            assistant_message_id = str(
                raw_turn.get("assistant_message_id")
                or getattr(execution, "assistant_message_id", None)
                or (continuation.assistant_message_id if continuation is not None else "")
                or f"{user_message_id}_reply"
            )
            base_history_head_id = str(raw_turn.get("base_history_head_id") or user_message_id)
            snapshot = payload.get("resolved_snapshot")
            if not isinstance(snapshot, Mapping):
                snapshot = latest_snapshot
            if not isinstance(snapshot, Mapping) or not snapshot:
                raise AgentDriverError("checkpoint_schema_invalid", "Agent safe point has no resolved snapshot")
            if latest_assistant is None:
                raise AgentDriverError("checkpoint_schema_invalid", "Agent safe point has no completed assistant message")
            tool_call_ids = [
                str(value) for value in payload.get("tool_call_ids", ())
            ]
            if not tool_call_ids:
                tool_call_ids = [
                    str(item.get("id"))
                    for item in latest_assistant.get("content", [])
                    if isinstance(item, Mapping) and item.get("type") == "toolCall"
                ]
            next_tool_index = payload.get("next_tool_index")
            if not isinstance(next_tool_index, int):
                next_tool_index = 0 if phase == "after_provider" else len(completed_tool_results)
            action_values = [*prior_actions]
            receipt_values = [*prior_receipts]
            if kind == "wait.before_tool":
                if not provider_effect_id or not provider_input_hash or provider_terminal_receipt is None:
                    raise AgentDriverError("checkpoint_schema_invalid", "wait has no committed provider decision")
                if not any(item.get("action_id") == provider_action_id for item in action_values):
                    action_values.append({"action_id": provider_action_id, "input_hash": provider_input_hash})
                if not any(item.get("effect_id") == provider_effect_id for item in receipt_values):
                    receipt_values.append({
                        "effect_id": provider_effect_id,
                        "frontier_step_id": f"after_provider:{provider_action_id}",
                        "action_id": provider_action_id,
                        "outcome": "committed",
                        "receipt": dict(provider_terminal_receipt),
                    })
            if effect_id is not None:
                if action_id is None or input_hash is None or terminal_receipt is None:
                    raise AgentDriverError("checkpoint_schema_invalid", "effect safe point is missing a receipt")
                action_values.append({"action_id": action_id, "input_hash": input_hash})
                receipt_values.append({
                    "effect_id": effect_id,
                    "frontier_step_id": f"{phase}:{action_id}",
                    "action_id": action_id,
                    "outcome": "committed",
                    "receipt": dict(terminal_receipt),
                })
            pending_commands = [
                command.command_id
                for command in self._control_service().executions.list_commands(attempt.execution_id)
                if command.status not in {CommandStatus.APPLIED, CommandStatus.REJECTED}
            ]
            try:
                return AgentCheckpointV1.build(
                    safe_point={
                        "kind": point_kind,
                        "step_id": (
                            f"wait:{payload.get('tool_call_id')}"
                            if kind == "wait.before_tool" else f"{phase}:{action_id}"
                        ),
                        "phase": phase,
                        "sentinel": "resume-from-checkpoint",
                    },
                    frontier=[{
                        "step_id": (
                            f"wait:{payload.get('tool_call_id')}"
                            if kind == "wait.before_tool" else f"{phase}:{action_id}"
                        ),
                        "phase": phase,
                        "branch_id": str(raw_turn.get("branch_id") or "main"),
                    }],
                    turn={
                        "user_message_id": user_message_id,
                        "assistant_message_id": assistant_message_id,
                        "base_history_head_id": base_history_head_id,
                    },
                    assistant_message=latest_assistant,
                    tool_results=completed_tool_results,
                    resolved_snapshot=dict(snapshot),
                    provider_action_id=provider_action_id,
                    tool_call_ids=tool_call_ids,
                    next_tool_index=next_tool_index,
                    repeat_failures=dict(payload.get("repeat_failures") or {}),
                    completed_actions=action_values,
                    terminal_effect_receipts=receipt_values,
                    pending_command_ids=pending_commands,
                )
            except AgentCheckpointError as exc:
                raise AgentDriverError(exc.code, str(exc)) from exc

        def hook(kind: str, payload: Mapping[str, Any]) -> bool:
            nonlocal provider_action_id, provider_effect_id, provider_input_hash, provider_terminal_receipt, latest_assistant, latest_snapshot, completed_tool_results
            service = self._control_service()
            if kind == "tool.before" and isinstance(payload.get("pre_wait"), Mapping):
                from openprogram.execution.checkpoints import CheckpointFragment
                from openprogram.execution.waits import DurableWaitStore, WaitStatus

                pre_wait = dict(payload["pre_wait"])
                tool_call_id = str(payload.get("tool_call_id") or "")
                if not provider_action_id or not tool_call_id:
                    raise AgentDriverError("checkpoint_schema_invalid", "wait has no stable provider or tool identity")
                wait_kind = str(pre_wait.get("kind") or "")
                if wait_kind not in {"approval", "ask", "ask_many", "confirm", "form"}:
                    raise AgentDriverError("invalid_wait", "wait kind is not supported at an Agent tool boundary")
                wait_id = "wait_" + digest(
                    attempt.execution_id, provider_action_id, tool_call_id, wait_kind,
                )[:32]
                existing = DurableWaitStore(self.executions).get_wait(wait_id)
                if existing is not None:
                    if existing.status is WaitStatus.RESOLVED:
                        payload["preapproved_wait_id"] = wait_id
                        return False
                    # Decline/timeout/cancel policies settle the execution
                    # before a replacement attempt can reach this boundary.
                    # Do not dispatch the protected tool from an unresolved
                    # or non-approved record.
                    return True
                checkpoint = checkpoint_payload("wait.before_tool", payload)
                current = service.executions.get_execution(attempt.execution_id)
                if current is None:
                    raise AgentDriverError("execution_not_found", "execution disappeared before wait")
                request_metadata = pre_wait.get("request_metadata", {})
                if not isinstance(request_metadata, Mapping):
                    raise AgentDriverError("invalid_wait", "wait request metadata is invalid")
                wait_request = {
                    "prompt": str(pre_wait.get("prompt") or ""),
                    "options": list(pre_wait.get("options") or ()),
                    "multi": bool(pre_wait.get("multi", False)),
                    "allow_custom": bool(pre_wait.get("allow_custom", True)),
                    "detail": str(pre_wait.get("detail") or ""),
                    "schema": dict(pre_wait.get("schema") or {}),
                    "questions": list(pre_wait.get("questions") or []),
                }
                reserved = set(wait_request).intersection(request_metadata)
                if reserved:
                    raise AgentDriverError("invalid_wait", "wait metadata cannot replace presentation fields")
                wait_request.update(dict(request_metadata))
                timeout = pre_wait.get("timeout", 300.0)
                if type(timeout) not in {int, float} or timeout <= 0:
                    raise AgentDriverError("invalid_wait", "approval wait timeout is invalid")
                policy = pre_wait.get("policy_snapshot")
                if not isinstance(policy, Mapping):
                    raise AgentDriverError("invalid_wait_policy", "approval wait policy is invalid")
                suspension = service.open_wait_at_safe_point(
                    execution_id=attempt.execution_id,
                    attempt_id=attempt.attempt_id,
                    generation=attempt.generation,
                    expected_version=current.status_version,
                    fragment=CheckpointFragment(
                        safe_point_kind="agent.wait.before_tool",
                        frontier=tuple(checkpoint.payload["frontier"]),
                        state_refs={},
                        completed_actions=tuple(), effect_receipts=tuple(),
                        pending_command_ids=tuple(checkpoint.payload["pending_command_ids"]),
                    ),
                    kind=wait_kind, request=wait_request,
                    policy_snapshot=dict(policy), expires_at=time.time() + float(timeout),
                    wait_id=wait_id, agent_checkpoint=checkpoint,
                )
                try:
                    if self.event_sink is None:
                        return True
                    self.event_sink({"type": "question.asked", "data": {
                        "id": suspension.wait.wait_id,
                        "session_id": request.session_id,
                        "kind": wait_kind, "prompt": wait_request["prompt"],
                        "options": wait_request["options"], "multi": wait_request["multi"],
                        "allow_custom": wait_request["allow_custom"], "detail": wait_request["detail"],
                        "schema": wait_request["schema"], "questions": wait_request["questions"],
                        "tool": wait_request.get("tool"), "args": wait_request.get("args"),
                        "risk_level": wait_request.get("risk_level"),
                        "execution_id": attempt.execution_id,
                        "wait_generation": suspension.wait.claim_generation,
                        "expected_version": suspension.execution.status_version,
                        "expires_at": suspension.wait.expires_at,
                    }})
                except Exception:
                    _log.exception("failed to publish durable approval wait")
                return True
            if kind.endswith(".before"):
                execution = service.executions.get_execution(attempt.execution_id)
                if execution is None:
                    raise AgentDriverError("execution_not_found", "execution disappeared before effect")
                if execution.status is ExecutionStatus.CANCELLING or cancel_event.is_set():
                    from openprogram.providers.utils.errors import ExecInterrupt
                    raise ExecInterrupt("cancelled")
                if kind == "provider.before":
                    snapshot = payload.get("resolved_snapshot")
                    if not isinstance(snapshot, Mapping):
                        raise AgentDriverError("checkpoint_schema_invalid", "provider action has no resolved snapshot")
                    latest_snapshot = dict(snapshot)
                    context_hash = str(payload.get("normalized_context_hash") or json_digest(payload.get("context") or {}))
                    action_id = digest(
                        str(execution.revision_id),
                        str(execution.checkpoint_head_id or "root"),
                        context_hash,
                        json_digest(latest_snapshot),
                    )
                    input_hash = context_hash
                elif kind == "tool.before":
                    tool_call_id = str(payload.get("tool_call_id") or "")
                    if not provider_action_id or not tool_call_id:
                        raise AgentDriverError("checkpoint_schema_invalid", "tool action has no provider decision")
                    action_id = digest(
                        str(execution.revision_id),
                        provider_action_id,
                        tool_call_id,
                        json_digest(payload.get("arguments") or {}),
                    )
                    input_hash = json_digest(payload.get("arguments") or {})
                else:
                    raise AgentDriverError("invalid_safe_point", "unsupported Agent effect boundary")
                effect_id = f"effect_{action_id[:32]}"
                supports_idempotency_key = (
                    kind == "provider.before"
                    and payload.get("supports_idempotency_key") is True
                )
                idempotency_key = action_id if supports_idempotency_key else None
                if kind == "provider.before":
                    # The callback mutates the request payload so the exact
                    # key used for the durable effect reaches SimpleStreamOptions.
                    payload["supports_idempotency_key"] = supports_idempotency_key
                    payload["idempotency_key"] = idempotency_key
                dispatch_candidates = tuple(
                    dict(candidate)
                    for candidate in (payload.get("dispatch_candidates") or ())
                    if isinstance(candidate, Mapping)
                )
                classification = (
                    EffectClassification.IDEMPOTENT
                    if supports_idempotency_key
                    else EffectClassification.NONREPEATABLE
                )
                effect = service.effects.register(
                    effect_id=effect_id, execution_id=attempt.execution_id,
                    attempt_id=attempt.attempt_id, action_id=action_id,
                    classification=classification,
                    idempotency_key=idempotency_key,
                    metadata={"kind": kind, "payload": dict(payload)},
                )
                if effect.status is EffectStatus.PLANNED:
                    service.effects.mark_dispatched(
                        effect.effect_id, expected_status=EffectStatus.PLANNED,
                    )
                pending[kind.rsplit(".", 1)[0]] = (
                    effect_id, action_id, input_hash, idempotency_key,
                    dispatch_candidates,
                )
                return False

            key = kind.rsplit(".", 1)[0]
            try:
                (
                    effect_id, action_id, input_hash, idempotency_key,
                    dispatch_candidates,
                ) = pending.pop(key)
            except KeyError as exc:
                raise AgentDriverError("effect_state_invalid", "Agent effect has no durable dispatch intent") from exc
            effect = service.effects.get(effect_id)
            if effect is None or effect.status is not EffectStatus.DISPATCHED:
                raise AgentDriverError("effect_state_invalid", "Agent effect is not dispatchable")
            if kind == "provider.after":
                message = payload.get("message")
                if not isinstance(message, Mapping):
                    raise AgentDriverError("checkpoint_schema_invalid", "provider receipt lacks AssistantMessage")
                latest_assistant = dict(message)
                provider_action_id = action_id
                completed_tool_results = []
                actual_identity = {
                    "api": message.get("api"),
                    "provider": message.get("provider"),
                    "model": message.get("model"),
                }
                actual_candidate = next(
                    (
                        candidate for candidate in dispatch_candidates
                        if all(
                            actual_identity[field] == candidate.get(field)
                            for field in ("api", "provider", "model")
                        )
                    ),
                    None,
                )
                actual_supports_idempotency_key = bool(
                    actual_candidate is not None
                    and actual_candidate.get("supports_idempotency_key") is True
                )
                if not dispatch_candidates:
                    # Direct callers predating candidate metadata have only
                    # the effect's dispatch-time capability declaration.
                    actual_supports_idempotency_key = idempotency_key is not None
                terminal_receipt = {
                    **actual_identity,
                    "provider_request_id": payload.get("provider_request_id"),
                    "usage": payload.get("usage"),
                    "message_hash": json_digest(latest_assistant),
                    "supports_idempotency_key": actual_supports_idempotency_key,
                }
                terminal_receipt["idempotency_key"] = (
                    idempotency_key if actual_supports_idempotency_key else None
                )
                provider_effect_id = effect_id
                provider_input_hash = input_hash
                provider_terminal_receipt = dict(terminal_receipt)
            elif kind == "tool.after":
                result = payload.get("result")
                if not isinstance(result, Mapping):
                    raise AgentDriverError("checkpoint_schema_invalid", "tool receipt lacks ToolResultMessage")
                completed_tool_results.append(dict(result))
                terminal_receipt = {
                    "tool_call_id": payload.get("tool_call_id"),
                    "is_error": bool(payload.get("is_error")),
                    "result_hash": json_digest(result),
                }
            else:
                raise AgentDriverError("invalid_safe_point", "unsupported Agent safe point")
            command = current_command(service, attempt.execution_id)
            if command is None:
                service.effects.resolve(
                    effect_id, expected_status=EffectStatus.DISPATCHED,
                    outcome=EffectStatus.COMMITTED, receipt=terminal_receipt,
                    attempt_id=attempt.attempt_id, generation=attempt.generation,
                )
                return False
            current = service.executions.get_execution(attempt.execution_id)
            if current is None:
                raise AgentDriverError("execution_not_found", "execution disappeared at safe point")
            checkpoint = checkpoint_payload(
                kind, payload, effect_id, action_id, input_hash, terminal_receipt,
            )
            completion = service.commit_agent_safe_point(
                execution_id=attempt.execution_id, attempt_id=attempt.attempt_id,
                generation=attempt.generation, expected_version=current.status_version,
                safe_point_kind=str(checkpoint.payload["safe_point"]["kind"]),
                frontier=tuple(checkpoint.payload["frontier"]), state_refs={},
                effect_id=effect_id, terminal_receipt=terminal_receipt,
                receipt_blob=canonical_json_bytes(terminal_receipt),
                agent_checkpoint=checkpoint,
                command_id=command.command_id, managed_action_id=action_id,
            )
            if command.kind is CommandKind.STEER and steer_queue is not None:
                consumed = steer_consumed_ids or set()
                for applied in completion.applied_commands:
                    if applied.kind is not CommandKind.STEER:
                        continue
                    if applied.command_id in consumed:
                        continue
                    message = applied.payload.get("message")
                    if isinstance(message, str) and message.strip():
                        steer_queue.append({
                            "command_id": applied.command_id,
                            "payload": {"message": message},
                        })
            return command.kind in {CommandKind.PAUSE, CommandKind.STEP}

        return hook

    def _finish_attempt(
        self,
        attempt: AttemptRecord,
        result: Any,
        cancel_event: threading.Event,
        *,
        failure_reason: str | None = None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        if getattr(result, "_execution_safe_point_handoff", False):
            return
        with self._handles_lock:
            already_finished = (
                key in self._finished or key in self._finish_repair_stalled
            )
        if already_finished:
            # A duplicate completion can arrive after the durable transition
            # succeeded. It must also resolve any in-process or persisted
            # repair state associated with that exact owner.
            self._resolve_finish_retry(key)
            return
        service = self._control_service()
        execution = service.executions.get_execution(attempt.execution_id)
        if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
            self._resolve_finish_retry(key)
            return
        if (
            execution.status is ExecutionStatus.PAUSED
            or execution.current_attempt_id != attempt.attempt_id
            or execution.owner_lease.get("generation") != attempt.generation
        ):
            # A successful safe-point transaction ended this exact owner.
            # Its producer may finish afterwards; that late return must not
            # manufacture a terminal completion or repair record.
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
        if isinstance(result, Mapping):
            failed = failed or bool(
                result.get("error") or result.get("killed") or result.get("page_cleanup_failed")
            )
        target = (
            ExecutionStatus.CANCELLED
            if cancelled
            else ExecutionStatus.FAILED
            if failed
            else ExecutionStatus.COMPLETED
        )
        outcome = "cancelled" if cancelled else "failed" if failed else "completed"
        if failed and failure_reason is None:
            failure_reason = "agent_runner_error"
        reason_code = (
            execution.reason_code or "cancelled"
            if cancelled
            else failure_reason
            if failed
            else None
        )
        with self._handles_lock:
            cancel_command_id = self._cancel_commands.get(key)
        try:
            service.finish_attempt(
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=execution.status_version,
                target=target,
                outcome=outcome,
                command_id=cancel_command_id,
                reason_code=reason_code,
            )
        except Exception:
            # Keep the completion eligible for a later recovery/retry. A
            # transient persistence failure must not be hidden by marking
            # the attempt finished before the durable transition succeeds.
            self._queue_finish_retry(
                attempt,
                execution.status_version,
                target,
                outcome,
                reason_code,
                cancel_command_id,
            )
            return
        self._resolve_finish_retry(key)

    def _queue_finish_retry(
        self,
        attempt: AttemptRecord,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        reason_code: str | None,
        command_id: str | None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        with self._handles_lock:
            self._pending_finishes[key] = (
                attempt, expected_execution_version, target, outcome, reason_code,
                command_id,
            )
        if self.executions is not None:
            self._persist_finish_retry(
                attempt, expected_execution_version, target, outcome,
                reason_code, command_id,
            )
        self._schedule_finish_retry_worker()

    def _retry_finish(self, key: tuple[str, str, int]) -> None:
        delay = 0.05
        retries = 0
        while True:
            retries += 1
            if retries > FINISH_RETRY_LIMIT:
                self._stall_finish_retry(key)
                return
            try:
                self._handles_lock.acquire()
                pending = self._pending_finishes.get(key)
                if pending is None or key in self._finished:
                    self._handles_lock.release()
                    return
                (
                    attempt, _expected_version, target, outcome, reason_code,
                    command_id,
                ) = pending
                self._handles_lock.release()
                try:
                    service = self._control_service()
                    execution = service.executions.get_execution(attempt.execution_id)
                except Exception:
                    time.sleep(delay)
                    delay = min(delay * 2, 1.0)
                    continue
                if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
                    self._resolve_finish_retry(key)
                    return
                if (
                    execution.current_attempt_id != attempt.attempt_id
                    or execution.owner_lease.get("generation") != attempt.generation
                ):
                    # A newer owner won the fence. The old completion must not
                    # overwrite it; the canonical record is already owned by
                    # recovery or the replacement attempt.
                    self._resolve_finish_retry(key)
                    return
                current_attempt = service.attempts.get(attempt.attempt_id)
                if (
                    current_attempt is None
                    or current_attempt.status is not AttemptStatus.ACTIVE
                    or current_attempt.generation != attempt.generation
                ):
                    self._resolve_finish_retry(key)
                    return
                retry_target = target
                retry_outcome = outcome
                retry_reason = reason_code
                if execution.status is ExecutionStatus.CANCELLING:
                    # Cancellation intent wins over a completion that was
                    # computed before the cancel CAS. Re-read the version on
                    # every attempt so the finish cannot reuse stale state.
                    retry_target = ExecutionStatus.CANCELLED
                    retry_outcome = "cancelled"
                    retry_reason = execution.reason_code or "cancelled"
                    # A finish may have failed before the cancel request was
                    # associated with this owner. Resolve the currently
                    # applying canonical cancel command before finishing, so
                    # the command cannot remain APPLYING after cancellation.
                    command_id = self._current_cancel_command_id(
                        service.executions,
                        execution.execution_id,
                        fallback=command_id,
                    )
                    if command_id is None:
                        time.sleep(delay)
                        delay = min(delay * 2, FINISH_RETRY_MAX_DELAY)
                        continue
                    with self._handles_lock:
                        current_pending = self._pending_finishes.get(key)
                        if current_pending is not None:
                            self._pending_finishes[key] = (
                                current_pending[0],
                                current_pending[1],
                                current_pending[2],
                                current_pending[3],
                                current_pending[4],
                                command_id,
                            )
                self._persist_finish_retry(
                    attempt,
                    execution.status_version,
                    retry_target,
                    retry_outcome,
                    retry_reason,
                    command_id,
                )
                try:
                    service.finish_attempt(
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        expected_execution_version=execution.status_version,
                        target=retry_target,
                        outcome=retry_outcome,
                        command_id=command_id,
                        reason_code=retry_reason,
                    )
                except AttemptConflict:
                    current = service.executions.get_execution(attempt.execution_id)
                    if (
                        current is None
                        or current.status in TERMINAL_EXECUTION_STATUSES
                        or current.current_attempt_id != attempt.attempt_id
                        or current.owner_lease.get("generation") != attempt.generation
                    ):
                        self._resolve_finish_retry(key)
                        return
                except Exception:
                    pass
                else:
                    self._resolve_finish_retry(key)
                    return
                time.sleep(delay)
                delay = min(delay * 2, FINISH_RETRY_MAX_DELAY)
            finally:
                pass

    def _persist_finish_retry(
        self,
        attempt: AttemptRecord,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        reason_code: str | None,
        command_id: str | None,
        retry_count: int = 0,
        next_attempt_at: float = 0.0,
    ) -> bool:
        if self.executions is None:
            return False
        try:
            self.executions.upsert_finish_repair(
                execution_id=attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
                expected_version=expected_execution_version,
                target=target.value,
                outcome=outcome,
                reason_code=reason_code,
                command_id=command_id,
                retry_count=retry_count,
                next_attempt_at=next_attempt_at,
            )
            with self._handles_lock:
                self._finish_repair_metrics["persisted"] += 1
            return True
        except Exception:
            # The bounded retry keeps the intent in memory and retries this
            # write on every pass; the single repair worker continues after
            # the initial eight attempts without creating one thread per key.
            with self._handles_lock:
                self._finish_repair_metrics["write_errors"] += 1
            return False

    def _stall_finish_retry(self, key: tuple[str, str, int]) -> None:
        with self._handles_lock:
            pending = self._pending_finishes.get(key)
        if pending is None:
            return
        attempt, expected_version, target, outcome, _reason_code, command_id = pending
        persisted = self._persist_finish_retry(
            attempt,
            expected_version,
            target,
            outcome,
            "finish_repair_stalled",
            command_id,
            retry_count=FINISH_RETRY_LIMIT,
            next_attempt_at=time.time() + FINISH_REPAIR_RETRY_TIMER_DELAY,
        )
        if not persisted:
            _log.error(
                "finish repair %s/%s/%s remains pending after retry budget; "
                "durable write is unavailable",
                attempt.execution_id,
                attempt.attempt_id,
                attempt.generation,
            )
            return
        with self._handles_lock:
            self._pending_finishes.pop(key, None)
            self._finish_repair_stalled.add(key)
            self._finished.add(key)
            self._finish_repair_metrics["stalled"] += 1

    def _schedule_finish_retry_worker(self) -> None:
        with self._handles_lock:
            if self._finish_retry_worker_active:
                return
            self._finish_retry_worker_active = True
        threading.Thread(
            target=self._run_finish_retry_worker,
            name="openprogram-agent-finish-repair",
            daemon=True,
        ).start()

    def _run_finish_retry_worker(self) -> None:
        try:
            with self._handles_lock:
                keys = tuple(self._pending_finishes)
            for key in keys:
                self._retry_finish(key)
            self._reconcile_stalled_repairs()
        finally:
            with self._handles_lock:
                self._finish_retry_worker_active = False
                pending = bool(self._pending_finishes)
            stalled = self.executions is not None and self.executions.has_stalled_finish_repairs()
            if pending or stalled:
                with self._handles_lock:
                    if self._finish_retry_timer is None:
                        timer = threading.Timer(
                            FINISH_REPAIR_RETRY_TIMER_DELAY,
                            self._finish_retry_timer_fired,
                        )
                        timer.daemon = True
                        self._finish_retry_timer = timer
                        timer.start()

    def _reconcile_stalled_repairs(self) -> None:
        if self.executions is None:
            return
        try:
            self._control_service().replay_finish_repairs(
                include_stalled=True, due_only=True,
            )
        except Exception:
            _log.exception("stalled Agent finish repair reconciliation failed")

    def _finish_retry_timer_fired(self) -> None:
        with self._handles_lock:
            self._finish_retry_timer = None
        self._schedule_finish_retry_worker()

    def _resolve_finish_retry(self, key: tuple[str, str, int]) -> None:
        self._delete_persisted_finish(key)
        with self._handles_lock:
            self._pending_finishes.pop(key, None)
            self._finish_repair_stalled.discard(key)
            self._finished.add(key)
            self._cancel_commands.pop(key, None)

    @staticmethod
    def _current_cancel_command_id(
        executions: ExecutionStore,
        execution_id: str,
        *,
        fallback: str | None,
    ) -> str | None:
        """Return the applying cancel command for the current execution."""
        commands = executions.list_commands(
            execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL,),
        )
        if commands:
            return commands[0].command_id
        if fallback:
            command = executions.get_command(fallback)
            if (
                command is not None
                and command.execution_id == execution_id
                and command.kind is CommandKind.CANCEL
                and command.status is CommandStatus.APPLYING
            ):
                return command.command_id
        return None

    def _delete_persisted_finish(self, key: tuple[str, str, int]) -> None:
        execution_id, attempt_id, generation = key
        if self.executions is not None:
            try:
                self.executions.delete_finish_repair(
                    execution_id, attempt_id, generation,
                )
            except Exception:
                pass

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
            self._continuation_start_gates.pop(key, None)
            self._continuation_committed.discard(key)
            self._finished.discard(key)
            self._finish_repair_stalled.discard(key)
            self._cancel_commands.pop(key, None)

    @staticmethod
    def _key(handle: AgentDriverHandle) -> tuple[str, str, int]:
        return handle.execution_id, handle.attempt_id, handle.generation

    @staticmethod
    def _default_turn_runner(
        *, request: Any, cancel_event: threading.Event,
        on_event: Callable[[dict], None] | None = None,
        execution_context: Mapping[str, Any] | None = None,
    ) -> Any:
        from openprogram.agent.dispatcher import process_user_turn
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


class CanonicalAgentEntry:
    """Internal durable admission and activation boundary for Agent turns.

    Public transports use this class for admission before acknowledgement or
    activation. It has no fallback to a message-derived execution id.
    """

    _ENTRYPOINT = "openprogram.agent.production_driver:AgentProductionDriver"
    _REVISION_MANIFEST = {"entrypoint": _ENTRYPOINT, "turn_input_schema": 1}

    def __init__(self, store: ExecutionStore, driver: AgentProductionDriver):
        if driver.executions is not store:
            raise ValueError("Agent driver must use the admission execution store")
        self.store = store
        self.driver = driver
        self.control = driver._control_service()

    def admit(
        self,
        *,
        session_id: str,
        turn_payload: Mapping[str, Any],
        trusted_actor: Mapping[str, Any],
        user_message_id: str | None,
        assistant_message_id: str | None,
        config_snapshot_ref: str,
    ) -> CanonicalAgentAdmission:
        payload = normalize_agent_turn_payload(turn_payload)
        supplied_session = (
            payload["request"].get("session_id")
            if payload["kind"] == "chat" else None
        )
        if supplied_session is not None and supplied_session != session_id:
            raise AgentDriverError(
                "input_session_mismatch", "Agent admission input belongs to another session"
            )
        encoded = _json_payload(payload)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        revision = self.store.create_revision(manifest=self._REVISION_MANIFEST)
        record = self.store.admit_execution(
            execution_id=f"exec_{uuid.uuid4().hex}",
            run_id=f"run_{uuid.uuid4().hex}",
            session_id=session_id,
            revision_id=revision.revision_id,
            input_ref=f"agent-turn:{content_hash}",
            input_hash=content_hash,
            entrypoint=self._ENTRYPOINT,
            trusted_actor=trusted_actor,
            config_snapshot_ref=config_snapshot_ref,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            capabilities=self.driver.capabilities_for_payload(payload),
            agent_turn_payload=payload,
        )
        return CanonicalAgentAdmission(
            execution_id=record.execution_id,
            session_id=record.session_id,
            status_version=record.status_version,
        )

    async def activate(self, admission: CanonicalAgentAdmission) -> CanonicalAgentActivation | None:
        # chat_ack precedes thread startup.  If a pause wins while that
        # thread is pending, its queued -> paused transition is the only
        # initial-activation handoff: this stale starter exits without
        # creating an attempt or a synthetic checkpoint.  A later continue
        # activates the same immutable admission input exactly once.
        current = self.store.get_execution(admission.execution_id)
        if (
            current is not None
            and current.status is ExecutionStatus.PAUSED
            and current.checkpoint_head_id is None
            and current.current_attempt_id is None
            and current.reason_code is None
            and current.status_version == admission.status_version + 1
        ):
            return None
        attempt, leased = self.control.attempts.lease(
            admission.execution_id,
            expected_version=admission.status_version,
            owner_id=f"agent-entry-{uuid.uuid4().hex}",
            ttl_seconds=30,
        )
        active, running = self.control.attempts.activate(
            attempt.attempt_id,
            generation=attempt.generation,
            expected_execution_version=leased.status_version,
        )
        delivered, issue = await self.control._activate(
            active, None, (), activator=self.driver.activate
        )
        if not delivered:
            # Activation failure is a durable owner loss. Let the control
            # service release the exact lease and classify the execution;
            # the public entry never writes lifecycle rows directly.
            try:
                self.control.recover_owner_loss(
                    active.execution_id,
                    attempt_id=active.attempt_id,
                    generation=active.generation,
                )
            except Exception:
                pass
            raise AgentDriverError(
                issue or "activation_failed", "canonical Agent activation failed"
            )
        return CanonicalAgentActivation(
            admission=admission,
            attempt_id=active.attempt_id,
            generation=active.generation,
            status_version=running.status_version,
        )

    async def activate_existing_job(
        self,
        execution_id: str,
        admission_id: str | None,
        expected_version: int,
    ) -> CanonicalAgentActivation | None:
        """Activate an already-admitted Job identity and immutable input only."""
        execution = self.store.get_execution(execution_id)
        if execution is None:
            raise AgentDriverError("execution_not_found", f"Job execution is missing: {execution_id}")
        if execution.status_version != expected_version:
            raise AgentDriverError("stale_version", "Job execution version is stale")
        resolved = self.driver.resolve_existing_job(execution_id)
        expected_admission = resolved.job_context["resource_hints"]["admission_id"]
        if expected_admission != admission_id:
            raise AgentDriverError("admission_mismatch", "Job admission id does not match immutable input")
        return await self.activate(CanonicalAgentAdmission(
            execution_id=execution.execution_id,
            session_id=execution.session_id,
            status_version=execution.status_version,
        ))


class CanonicalAgentAdapter:
    """Transport-neutral adapter for durable Agent chat admission/activation."""

    def __init__(
        self,
        *,
        store: ExecutionStore | None = None,
        event_sink: Callable[[dict], None] | None = None,
        turn_runner: TurnRunner | None = None,
        question_registry: Any | None = None,
    ) -> None:
        from openprogram.execution import default_control_service, default_store

        self.store = store or default_store()
        self.driver = AgentProductionDriver(
            self.store,
            control_service=default_control_service(),
            event_sink=event_sink,
            turn_runner=turn_runner,
            question_registry=question_registry,
        )
        self.entry = CanonicalAgentEntry(self.store, self.driver)

    @staticmethod
    def payload_for(request: Any) -> dict[str, Any]:
        """Build the strict durable chat envelope from a TurnRequest."""
        from openprogram.agent.dispatcher.types import INHERIT_PARENT

        inherit_parent = getattr(request, "branch_from", None) is INHERIT_PARENT
        values = asdict(request) if is_dataclass(request) else dict(request)
        if inherit_parent:
            values.pop("branch_from", None)
        return {
            "version": AGENT_TURN_INPUT_VERSION,
            "kind": "chat",
            "request": _json_safe(values),
        }

    def admit(
        self,
        request: Any,
        *,
        trusted_actor: Mapping[str, Any],
        user_message_id: str | None,
        assistant_message_id: str | None = None,
        config_snapshot_ref: str,
    ) -> CanonicalAgentAdmission:
        return self.admit_payload(
            session_id=request.session_id,
            payload=self.payload_for(request),
            trusted_actor=trusted_actor,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            config_snapshot_ref=config_snapshot_ref,
        )

    def admit_payload(
        self,
        *,
        session_id: str,
        payload: Mapping[str, Any],
        trusted_actor: Mapping[str, Any],
        user_message_id: str | None,
        assistant_message_id: str | None = None,
        config_snapshot_ref: str,
    ) -> CanonicalAgentAdmission:
        return self.entry.admit(
            session_id=session_id,
            turn_payload=payload,
            trusted_actor=trusted_actor,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            config_snapshot_ref=config_snapshot_ref,
        )

    async def activate(
        self,
        admission: CanonicalAgentAdmission,
        *,
        on_activated: Callable[[CanonicalAgentActivation], None] | None = None,
    ) -> Any:
        active = await self.entry.activate(admission)
        if active is None:
            return None
        if on_activated is not None:
            on_activated(active)
        handle = self.driver._handles[
            (active.admission.execution_id, active.attempt_id, active.generation)
        ]
        result = await handle.done
        return active, result

    def fail_admission(
        self,
        admission: CanonicalAgentAdmission,
        *,
        reason_code: str,
        target: ExecutionStatus = ExecutionStatus.FAILED,
    ) -> None:
        self.driver.fail_admission(
            admission, reason_code=reason_code, target=target,
        )


__all__ = [
    "AgentActivationService",
    "AgentDriverError",
    "AgentDriverHandle",
    "CanonicalAgentActivation",
    "CanonicalAgentAdmission",
    "CanonicalAgentAdapter",
    "CanonicalAgentEntry",
    "ForcedToolActivation",
    "JobAgentActivation",
    "AGENT_TURN_INPUT_VERSION",
    "MAX_AGENT_TURN_INPUT_BYTES",
    "normalize_agent_turn_payload",
    "AgentProductionDriver",
]
