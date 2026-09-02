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
    done: asyncio.Future[Any]


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


InputResolver = Callable[[Any], Mapping[str, Any]]
TurnRunner = Callable[..., Any]

AGENT_TURN_INPUT_VERSION = 1
MAX_AGENT_TURN_INPUT_BYTES = 256 * 1024
AGENT_CHECKPOINT_SCHEMA_VERSION = 1
MAX_AGENT_CHECKPOINT_BYTES = 256 * 1024
MAX_AGENT_STATE_BLOB_BYTES = 1024 * 1024
MAX_AGENT_STATE_REFS = 32
MAX_AGENT_PENDING_MESSAGES = 64
MAX_AGENT_TERMINAL_EFFECT_RECEIPTS = 64
MAX_AGENT_DELTA_BYTES = 64 * 1024
MAX_AGENT_REPEAT_FAILURES = 16
AGENT_SAFE_POINT_KINDS = (
    "agent.provider.decision.after",
    "agent.tool.action.after",
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
        input_resolver: InputResolver | None = None,
        turn_runner: TurnRunner | None = None,
        control_service: RuntimeControlService | None = None,
        question_registry: Any | None = None,
        event_sink: Callable[[dict], None] | None = None,
        activation_observer: Callable[[ActivationInput], None] | None = None,
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
        self._captured_safe_points: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._handles: dict[tuple[str, str, int], AgentDriverHandle] = {}
        self._handles_lock = threading.RLock()
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

    @staticmethod
    def capabilities_for_payload(payload: Mapping[str, Any]) -> CapabilitySet:
        """Return the admitted capability contract, never a transport guess."""
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
        if activation is not None and activation.checkpoint is not None:
            if self.activation_observer is not None:
                self.activation_observer(activation)
            done = asyncio.get_running_loop().create_future()
            done.set_result({"resumed": True})
            handle = AgentDriverHandle(attempt.execution_id, attempt.attempt_id, attempt.generation, record.session_id, threading.Event(), done)
            with self._handles_lock:
                self._handles[self._key(handle)] = handle
            return DriverBinding(attempt.execution_id, attempt.attempt_id, attempt.generation, self, handle)
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
    ) -> Any:
        try:
            result = await asyncio.to_thread(
                self._run_turn,
                attempt,
                request,
                cancel_event,
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
            if isinstance(request, ForcedToolActivation):
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
                            ),
                        }
                    if "on_safe_point" in parameters:
                        def _captured(**value):
                            phase = value.get("phase")
                            for name, item in value.items():
                                if name == "tool_call" and phase == "after_provider":
                                    continue
                                if callable(item):
                                    item()
                            self._captured_safe_points[(attempt.execution_id, attempt.attempt_id, attempt.generation)] = dict(value)
                        runner_kwargs["on_safe_point"] = _captured
                except (TypeError, ValueError):
                    pass
                result = self.turn_runner(**runner_kwargs)
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

    def _safe_point_hook(
        self,
        attempt: AttemptRecord,
        request: Any,
        cancel_event: threading.Event,
    ) -> Callable[[str, Mapping[str, Any]], bool]:
        """Bind Agent-loop boundaries to the canonical execution owner.

        The closure carries only durable identifiers and JSON payloads.  It
        deliberately retains no provider stream, coroutine, tool object, or
        dispatcher-local state for a future attempt.
        """
        from openprogram.execution.checkpoints import CheckpointFragment
        from openprogram.execution.effects import (
            EffectClassification, EffectStatus,
        )
        from openprogram.execution.model import CommandKind

        pending: dict[str, tuple[str, str]] = {}

        def digest(*parts: str) -> str:
            value = "\x1f".join(parts).encode("utf-8")
            return hashlib.sha256(value).hexdigest()

        def current_command(service, execution_id: str):
            commands = service.executions.list_commands(execution_id)
            priority = (CommandKind.PAUSE, CommandKind.STEP)
            for kind in priority:
                for command in commands:
                    if command.kind is kind and command.status is CommandStatus.APPLYING:
                        return command
            return None

        def checkpoint_payload(kind: str, payload: Mapping[str, Any], effect_id: str, action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
            phase = "after_provider" if kind == "provider.after" else "after_tool"
            point_kind = (
                "agent.provider.decision.after" if phase == "after_provider"
                else "agent.tool.action.after"
            )
            user_message_id = str(getattr(request, "user_msg_id", "") or "durable-user")
            assistant_message_id = user_message_id + "_reply"
            state = {
                "schema_version": AGENT_CHECKPOINT_SCHEMA_VERSION,
                "safe_point": {
                    "kind": point_kind, "step_id": f"{phase}:{action_id}",
                    "phase": phase, "sentinel": "resume-from-checkpoint",
                },
                "frontier": [{"step_id": f"{phase}:{action_id}", "phase": phase, "branch_id": "main"}],
                "turn": {
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "base_history_head_id": user_message_id,
                },
                "current_decision": {
                    "provider_action_id": action_id if phase == "after_provider" else "",
                    "assistant_message_ref": assistant_message_id,
                    "tool_call_ids": sorted(str(value) for value in payload.get("tool_call_ids", ())),
                },
                "next_tool_index": 0,
                "repeat_failures": 0,
                "pending_messages": [], "completed_actions": [{"action_id": action_id}],
                "terminal_effect_receipts": [{
                    "effect_id": effect_id, "frontier_step_id": f"{phase}:{action_id}",
                    "action_id": action_id, "outcome": "committed",
                }],
                "payload": dict(payload),
            }
            raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(raw.encode("utf-8")) > MAX_AGENT_CHECKPOINT_BYTES:
                raise AgentDriverError("checkpoint_too_large", "Agent checkpoint exceeds the size limit")
            return state, {
                "safe_point_kind": point_kind,
                "frontier": tuple(state["frontier"]),
                "completed_actions": tuple(state["completed_actions"]),
                "effect_receipts": tuple(state["terminal_effect_receipts"]),
            }

        def hook(kind: str, payload: Mapping[str, Any]) -> bool:
            service = self._control_service()
            if kind.endswith(".before"):
                normalized = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str)
                execution = service.executions.get_execution(attempt.execution_id)
                if execution is None:
                    raise AgentDriverError("execution_not_found", "execution disappeared before effect")
                # A restarted continuation must name the same logical action.
                # Attempts/generations are leases, not action identity.
                action_id = digest(
                    attempt.execution_id, str(execution.revision_id),
                    str(execution.checkpoint_head_id or "root"), kind, normalized,
                    str(getattr(request, "provider", "")), str(getattr(request, "model", "")),
                )
                effect_id = f"effect_{action_id[:32]}"
                classification = (
                    EffectClassification.IDEMPOTENT if kind == "provider.before"
                    else EffectClassification.NONREPEATABLE
                )
                effect = service.effects.register(
                    effect_id=effect_id, execution_id=attempt.execution_id,
                    attempt_id=attempt.attempt_id, action_id=action_id,
                    classification=classification,
                    idempotency_key=action_id if classification is EffectClassification.IDEMPOTENT else None,
                    metadata={"kind": kind, "payload": dict(payload)},
                )
                if effect.status is EffectStatus.PLANNED:
                    service.effects.mark_dispatched(
                        effect.effect_id, expected_status=EffectStatus.PLANNED,
                    )
                pending[kind.rsplit(".", 1)[0]] = (effect_id, action_id)
                return False

            key = kind.rsplit(".", 1)[0]
            effect_id, action_id = pending.pop(key)
            effect = service.effects.get(effect_id)
            if effect is None or effect.status is not EffectStatus.DISPATCHED:
                raise AgentDriverError("effect_state_invalid", "Agent effect is not dispatchable")
            state, fragment_data = checkpoint_payload(kind, payload, effect_id, action_id)
            command = current_command(service, attempt.execution_id)
            if command is None:
                service.effects.resolve(
                    effect_id, expected_status=EffectStatus.DISPATCHED,
                    outcome=EffectStatus.COMMITTED, receipt={"payload": dict(payload)},
                    attempt_id=attempt.attempt_id, generation=attempt.generation,
                )
                return False
            current = service.executions.get_execution(attempt.execution_id)
            if current is None:
                raise AgentDriverError("execution_not_found", "execution disappeared at safe point")
            if command.kind is CommandKind.STEP:
                service.consume_agent_step_permit(
                    execution_id=attempt.execution_id, command_id=command.command_id,
                    action_id=action_id,
                )
            service.commit_agent_safe_point(
                execution_id=attempt.execution_id, attempt_id=attempt.attempt_id,
                generation=attempt.generation, expected_version=current.status_version,
                safe_point_kind=fragment_data["safe_point_kind"], frontier=fragment_data["frontier"],
                state_refs=state, effect_id=effect_id, terminal_receipt={"payload": dict(payload)},
                receipt_blob=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
                checkpoint_state_blob=json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
                command_id=command.command_id,
            )
            return True

        return hook

    async def publish_safe_point(self, binding, *, safe_point_kind: str, frontier):
        from openprogram.execution.checkpoints import CheckpointFragment
        execution = self.executions.get_execution(binding.execution_id)
        if execution is None:
            raise AgentDriverError("execution_not_found", "execution missing")
        checkpoint, updated = self._control_service().checkpoints.publish(
            binding.execution_id, expected_version=execution.status_version,
            revision_id=execution.revision_id, parent_checkpoint_id=execution.checkpoint_head_id,
            frontier=frontier, state_refs={"safe_point": {"kind": safe_point_kind, "phase": frontier[-1]["phase"], "sentinel": "resume-from-checkpoint"}},
            completed_actions=(), effect_receipts=(), child_frontier={}, pending_command_ids=(),
            created_by_attempt_id=binding.attempt_id,
        )
        with self.executions._transaction() as connection:
            pausing = self.executions._transition_execution(
                connection, updated.execution_id, expected_version=updated.status_version,
                target=ExecutionStatus.PAUSING, reason_code=None,
            )
            paused = self.executions._transition_execution(
                connection, pausing.execution_id, expected_version=pausing.status_version,
                target=ExecutionStatus.PAUSED, reason_code=None, clear_owner=True,
            )
        # This helper is used by the production driver only for an explicit
        # declared safe point.  End the old lease before another activation
        # can claim the checkpoint.
            current = self._control_service().attempts._require(connection, binding.attempt_id)
            if current.status is AttemptStatus.ACTIVE:
                self._control_service().attempts._end_for_owner_loss(
                    connection, current, outcome="paused_at_safe_point"
                )
        return SimpleNamespace(checkpoint=checkpoint, execution=paused)

    async def run_until_safe_point(self, binding, *, safe_point_kind: str):
        await binding.handle.done
        phase = safe_point_kind.rsplit(".", 1)[-1].replace("decision.after", "after_provider").replace("action.after", "after_tool")
        phase = "after_provider" if "provider" in safe_point_kind else "after_tool"
        return await self.publish_safe_point(binding, safe_point_kind=safe_point_kind, frontier=({"step_id": phase, "phase": phase},))

    async def enter_wait(self, binding, *, kind: str, request_id: str):
        return SimpleNamespace(execution=self.executions.get_execution(binding.execution_id), kind=kind, request_id=request_id)

    async def request_pause_at_wait(self, binding, *, command_id: str):
        execution = self.executions.get_execution(binding.execution_id)
        if execution is None:
            raise AgentDriverError("execution_not_found", "execution missing")
        return self.executions.transition_execution(execution.execution_id, expected_version=execution.status_version, target=ExecutionStatus.PAUSING)

    def _finish_attempt(
        self,
        attempt: AttemptRecord,
        result: Any,
        cancel_event: threading.Event,
        *,
        failure_reason: str | None = None,
    ) -> None:
        key = (attempt.execution_id, attempt.attempt_id, attempt.generation)
        if key in self._captured_safe_points:
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
            "cancelled"
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
    ) -> Any:
        from openprogram.agent.dispatcher import process_user_turn
        kwargs = {}
        try:
            params = inspect.signature(process_user_turn).parameters
            if "on_event" in params:
                kwargs["on_event"] = on_event
            if "cancel_event" in params:
                kwargs["cancel_event"] = cancel_event
        except (TypeError, ValueError):
            kwargs = {"on_event": on_event, "cancel_event": cancel_event}
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

    async def activate(self, admission: CanonicalAgentAdmission) -> CanonicalAgentActivation:
        attempt, leased = self.control.attempts.lease(
            admission.execution_id,
            expected_version=admission.status_version,
            owner_id=f"agent-entry-{uuid.uuid4().hex}",
            ttl_seconds=30,
        )
        active, _running = self.control.attempts.activate(
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
        )


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

    async def activate(self, admission: CanonicalAgentAdmission) -> Any:
        active = await self.entry.activate(admission)
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


async def cancel_canonical_execution(
    execution_id: str, *, reason_code: str = "cancel.user",
) -> Any | None:
    """Cancel one canonical execution through the control service."""
    from types import SimpleNamespace

    from openprogram.agent.authority import local_owner_authority
    from openprogram.execution import default_control_service, default_store
    from openprogram.execution.attempts import AttemptConflict
    from openprogram.execution.store import ExecutionConflict

    store = default_store()
    service = default_control_service()
    for attempt_number in range(2):
        execution = store.get_execution(execution_id)
        if execution is None:
            return None
        if execution.status in TERMINAL_EXECUTION_STATUSES or execution.status is ExecutionStatus.CANCELLING:
            return SimpleNamespace(execution=execution)
        try:
            return await service.request_cancel(
                command_id=f"cancel_{uuid.uuid4().hex}",
                execution_id=execution_id,
                expected_version=execution.status_version,
                actor=local_owner_authority(),
                reason_code=reason_code,
            )
        except (AttemptConflict, ExecutionConflict):
            latest = store.get_execution(execution_id)
            if latest is None:
                return None
            if (
                latest.status in TERMINAL_EXECUTION_STATUSES
                or latest.status is ExecutionStatus.CANCELLING
            ):
                return SimpleNamespace(execution=latest)
            if attempt_number == 0:
                continue
            raise
    return None


__all__ = [
    "AgentActivationService",
    "AgentDriverError",
    "AgentDriverHandle",
    "CanonicalAgentActivation",
    "CanonicalAgentAdmission",
    "CanonicalAgentAdapter",
    "CanonicalAgentEntry",
    "ForcedToolActivation",
    "AGENT_TURN_INPUT_VERSION",
    "MAX_AGENT_TURN_INPUT_BYTES",
    "normalize_agent_turn_payload",
    "cancel_canonical_execution",
    "AgentProductionDriver",
]
