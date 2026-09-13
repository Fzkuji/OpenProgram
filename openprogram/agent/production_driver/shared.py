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


from contextlib import contextmanager


from concurrent.futures import Future


from dataclasses import asdict, dataclass, fields, is_dataclass, replace


from types import SimpleNamespace


from typing import Any, Callable, Mapping


from openprogram.execution.attempts import (
    AttemptConflict,
    AttemptRecord,
    AttemptStatus,
    AttemptStore,
)


from openprogram.execution.agent_input_budget import (
    AGENT_IMAGE_BYTES_MAX,
    AGENT_TURN_IMAGE_BYTES_MAX,
    AGENT_TURN_INPUT_MAX_BYTES,
    AgentInputBudgetError,
    budget_payload,
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
    decode_turn_display,
    validate_runtime_contract,
)


_log = logging.getLogger(__name__)


AGENT_LEASE_SECONDS = 30.0


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


@dataclass(frozen=True)
class _SafePointHandoff:
    """Marker returned after publishing a durable execution wait."""

    _execution_safe_point_handoff: bool = True
    failed: bool = False


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


MAX_AGENT_TURN_INPUT_BYTES = AGENT_TURN_INPUT_MAX_BYTES


MAX_AGENT_IMAGE_BYTES = AGENT_IMAGE_BYTES_MAX


MAX_AGENT_TURN_IMAGE_BYTES = AGENT_TURN_IMAGE_BYTES_MAX


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
    # Image media is already carried separately from instructions. Give only
    # validated image data its own bounded budget; names and all other fields
    # still count toward the normal text/tool-input limit.
    try:
        budget_value = budget_payload(value)
    except AgentInputBudgetError as exc:
        raise AgentDriverError(exc.code, str(exc)) from exc
    encoded = _json_payload(budget_value)
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


class CanonicalAgentEntry:
    """Internal durable admission and activation boundary for Agent turns.

    Public transports use this class for admission before acknowledgement or
    activation. It has no fallback to a message-derived execution id.
    """

    _ENTRYPOINT = "openprogram.agent.production_driver:AgentProductionDriver"
    _REVISION_MANIFEST = {"entrypoint": _ENTRYPOINT, "turn_input_schema": 1}

    def __init__(self, store: ExecutionStore, driver: AgentProductionDriver):
        from .service import AgentProductionDriver
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
            track_process_owner=True,
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
            ttl_seconds=AGENT_LEASE_SECONDS,
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
        from .service import AgentProductionDriver
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

