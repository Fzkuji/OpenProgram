"""AgentProductionDriver activation operations."""
from __future__ import annotations

import logging
from dataclasses import replace
from openprogram.runtime.conversation.checkpoint import AgentContinuation
import time
from openprogram.runtime.conversation.production_driver import shared
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution.driver import (
    DriverAck,
    DriverBinding,
    RuntimeSnapshot,
    TerminationReceipt,
)
from openprogram.runtime.execution.attempts import AttemptRecord, AttemptStatus
from openprogram.runtime.execution.agent_journal import AGENT_STATE_SCHEMA_VERSION
from openprogram.runtime.execution.admission_input.runtime_contract import (
    RuntimeContractError,
    validate_runtime_contract,
)
from openprogram.runtime.execution.control.service import RuntimeControlService
from openprogram.runtime.execution.model import ExecutionStatus
from openprogram.runtime.execution.driver import ActivationInput

_log = logging.getLogger(__name__)


class ActivationOperations:
    def _resolve_durable_input(self, record: shared.Any) -> shared.Mapping[str, shared.Any]:
        if self.executions is None:
            raise shared.AgentDriverError("store_required", "Agent activation requires an execution store")
        payload = self.executions.get_agent_turn_input(record.execution_id)
        if payload is None:
            payload = self.executions.get_job_agent_input(record.execution_id)
        if payload is None:
            raise shared.AgentDriverError(
                "input_not_found",
                f"durable Agent turn input is missing for {record.execution_id}",
            )
        return payload


    def _resolve_activation_input(
        self, record: shared.Any, activation: ActivationInput | None,
    ) -> shared.Any:
        payload = self.activation._input_resolver(record)
        if not isinstance(payload, shared.Mapping):
            raise shared.AgentDriverError("invalid_input", "Agent admission input must resolve to an object")
        if payload.get("kind") == "job_agent":
            resolved = self.activation.build_job_activation(record)
            if record.assistant_message_id == f"{record.user_message_id}_reply":
                resolved.request.user_msg_id = record.user_message_id
            if self.job_resume_resolver is not None:
                resume_parent = self.job_resume_resolver(record.execution_id)
                if resume_parent is not None:
                    resolved.request.branch_from = resume_parent
            setattr(resolved.request, "_job_context", resolved.job_context)
            return resolved.request
        envelope = shared.normalize_agent_turn_payload(payload)
        if envelope["kind"] == "chat":
            return self.activation.build_request(record, activation)
        if activation is not None and activation.steer_inputs:
            raise shared.AgentDriverError(
                "unsupported_activation_state",
                "forced-tool activations do not support steering",
            )
        if activation is not None and activation.checkpoint is not None:
            marker = activation.checkpoint.state_refs.get("forced_tool") or activation.checkpoint.state_refs.get("function")
            if not isinstance(marker, shared.Mapping) or marker.get("version") != 1:
                raise shared.AgentDriverError(
                    "unsupported_activation_state",
                    "forced-tool activation checkpoint is not a system access handoff",
                )
        return shared.ForcedToolActivation(
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


    def resolve_existing_job(self, execution_id: str) -> shared.JobAgentActivation:
        """Resolve existing immutable Job input without admitting an execution."""
        if self.executions is None:
            raise shared.AgentDriverError("store_required", "Job activation requires an execution store")
        record = self.executions.get_execution_input(execution_id)
        if record is None:
            raise shared.AgentDriverError("input_not_found", f"immutable Job input is missing for {execution_id}")
        if self.executions.get_job_agent_input(execution_id) is None:
            raise shared.AgentDriverError("wrong_input_kind", f"execution {execution_id} is not a Job Agent input")
        return self.activation.build_job_activation(record)


    @staticmethod
    def capabilities_for_payload(payload: shared.Mapping[str, shared.Any]) -> CapabilitySet:
        """Return the admitted capability contract, never a transport guess."""
        if not isinstance(payload, shared.Mapping):
            raise shared.AgentDriverError("invalid_input", "Agent admission input must be an object")
        if payload.get("kind") == "job_agent":
            # Job input has its own strict envelope.  A Job is still driven by
            # the same Agent loop, so it exposes the same provider/tool safe
            # points and durable steering contract as ordinary chat.
            from openprogram.runtime.execution.admission_input.job_input import JobAgentInputError
            from openprogram.runtime.jobs.input import JobAgentInputV1

            try:
                JobAgentInputV1.parse(payload)
            except JobAgentInputError as exc:
                raise shared.AgentDriverError("invalid_job_input", str(exc)) from exc
            return CapabilitySet(
                pause=True,
                step=True,
                steer=True,
                fork=True,
                retry=True,
                safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
                state_schema_version=AGENT_STATE_SCHEMA_VERSION,
            )
        envelope = shared.normalize_agent_turn_payload(payload)
        if envelope["kind"] != "chat":
            tool_name = str(envelope.get("tool_name") or "")
            tool_input = envelope.get("tool_input")
            # A tool that needs desktop grants may stop before it runs to wait for them.
            from openprogram.host.system_access import _desktop_operation
            desktop_wait = isinstance(tool_input, shared.Mapping) and _desktop_operation(
                tool_name, dict(tool_input)) is not None
            from openprogram.programs._host.runtime import get
            tool = get(tool_name)
            if tool is not None and getattr(tool, "_resumable", False):
                return CapabilitySet(pause=True, safe_point_kinds=(
                    ("function.step.after", "agent.wait.before_tool") if desktop_wait else ("function.step.after",)
                ), state_schema_version=1)
            if not desktop_wait:
                return CapabilitySet()
            return CapabilitySet(
                pause=True,
                safe_point_kinds=("agent.wait.before_tool",),
                state_schema_version=AGENT_STATE_SCHEMA_VERSION,
            )
        from openprogram.runtime.agent.turn_request import slash_action_of
        request = envelope["request"]
        text = str(request.get("user_text") or "").lstrip()
        if (
            slash_action_of(request) is not None
            or text.startswith(("/forced_tool", "/spawn", "/merge"))
        ):
            return CapabilitySet()
        from openprogram.runtime.harnesses import is_harness_provider, turn_provider
        if is_harness_provider(turn_provider(request)):
            # An outside agent has no safe point to pause, step or fork at;
            # steering goes to it while it runs.
            return CapabilitySet(steer=True)
        return CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
            state_schema_version=AGENT_STATE_SCHEMA_VERSION,
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
            safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
            state_schema_version=AGENT_STATE_SCHEMA_VERSION,
        )


    async def activate(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput | None,
    ) -> DriverBinding[shared.AgentDriverHandle]:
        """Start the attempt's owner thread and return its binding.

        Everything before that reads or writes the conversation (its
        journal, its nodes, its model pin) under the conversation's lock,
        so it runs in a worker thread: a continue or an answer awaited on
        the Worker's event loop does not hold the loop while it waits."""
        from openprogram.runtime.execution.control.shared import off_loop

        return await off_loop(attempt.execution_id, self._activate_blocking, attempt, activation)

    def _activate_blocking(
        self,
        attempt: AttemptRecord,
        activation: ActivationInput | None,
    ) -> DriverBinding[shared.AgentDriverHandle]:
        if self.executions is None:
            raise shared.AgentDriverError("store_required", "Agent activation requires an execution store")
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise shared.AgentDriverError("execution_not_found", f"execution not found: {attempt.execution_id}")
        if (
            attempt.status is not AttemptStatus.ACTIVE
            or execution.status is not ExecutionStatus.RUNNING
            or execution.current_attempt_id != attempt.attempt_id
            or execution.owner_lease.get("generation") != attempt.generation
        ):
            raise shared.AgentDriverError(
                "stale_attempt",
                "Agent activation does not match the durable execution owner",
            )
        record = self.executions.get_execution_input(attempt.execution_id)
        if record is None:
            raise shared.AgentDriverError(
                "input_not_found",
                f"immutable Agent input is missing for {attempt.execution_id}",
            )
        if record.session_id != execution.session_id:
            raise shared.AgentDriverError(
                "input_session_mismatch",
                "immutable Agent input does not match the execution session",
            )
        request = self._resolve_activation_input(record, activation)
        # Forced-tool inputs are frozen and cannot create Agent continuation
        # checkpoints; only mutable chat/Job requests need this runtime field.
        if not isinstance(request, shared.ForcedToolActivation):
            setattr(request, "_execution_revision_id", execution.revision_id)
            if execution.parent_execution_id is not None and record.assistant_message_id == f"{execution.execution_id}_reply":
                request.advance_head = False
        steer_inputs = tuple(activation.steer_inputs) if activation is not None else ()
        continuation = None
        if activation is not None and activation.checkpoint is not None and not isinstance(request, shared.ForcedToolActivation):
            try:
                continuation = AgentContinuation.from_checkpoint(
                    store=self.executions,
                    checkpoint=activation.checkpoint,
                    request=request,
                )
                if (execution.parent_execution_id is not None
                        and activation.checkpoint.execution_id != execution.execution_id):
                    with self.executions._transaction() as connection:
                        branch = RuntimeControlService._agent_branch_checkpoint_is_valid(
                            self.executions, connection, execution, activation.checkpoint,
                        )
                    if not branch:
                        raise shared.AgentDriverError("invalid_checkpoint", "Agent branch source checkpoint is invalid")
                    if record.assistant_message_id != f"{execution.execution_id}_reply":
                        raise shared.AgentDriverError("checkpoint_schema_invalid", "Agent branch assistant anchor is invalid")
                    branch_snapshot = shared.copy.deepcopy(dict(continuation.resolved_snapshot))
                    # The validated manifest permits only this revision identity
                    # change; model, tools, prompt and authority remain exact.
                    branch_snapshot["request_semantics"]["_execution_revision_id"] = execution.revision_id
                    continuation = replace(
                        continuation, assistant_message_id=record.assistant_message_id,
                        resolved_snapshot=branch_snapshot,
                    )
                    from openprogram.runtime.context.session_db import default_db
                    from openprogram.state.store.session.nodes import Call, ROLE_LLM
                    from openprogram.state.store import SessionNodeWriter
                    db = default_db()
                    if not db.message_exists(record.session_id, record.assistant_message_id):
                        SessionNodeWriter(db, record.session_id, advance_head=False).append(Call(
                            id=record.assistant_message_id, role=ROLE_LLM, output="",
                            predecessor=record.user_message_id, created_at=time.time(),
                            metadata={"agent_id": request.agent_id, "execution_id": execution.execution_id},
                        ))
                if (
                    record.user_message_id != continuation.user_message_id
                    or record.assistant_message_id != continuation.assistant_message_id
                ):
                    raise shared.AgentDriverError(
                        "checkpoint_schema_invalid",
                        "Agent checkpoint branch anchors differ from immutable admission input",
                    )
                from openprogram.runtime.context.session_db import default_db
                from openprogram.state.store import SessionNodeWriter
                assistant_node = SessionNodeWriter(default_db(), record.session_id).load().nodes.get(record.assistant_message_id)
                if assistant_node and assistant_node.predecessor != record.user_message_id:
                    request._steering_tail_id = assistant_node.predecessor
                from openprogram.runtime.conversation.turn_runtime.loop_runner import resolve_agent_runtime
                from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
                from openprogram.state.worktree.context import reset_worktree, set_worktree
                _location = runtime_location_for(
                    request.session_id, use_context=False,
                    branch_id=getattr(request, "branch_id", None),
                )
                _workdir_token = set_worktree(_location["workdir"])
                try:
                    _profile, _tools, _recordable, _prompt, _model, _contract = resolve_agent_runtime(
                        request,
                        assistant_msg_id=continuation.assistant_message_id,
                        saved_runtime_contract=continuation.resolved_snapshot,
                    )
                finally:
                    reset_worktree(_workdir_token)
                from openprogram.runtime.agent.durable_steps import retained_function_names
                validate_runtime_contract(continuation.resolved_snapshot, _contract, durable_function_names=retained_function_names(self.executions, execution.execution_id))
            except RuntimeContractError as exc:
                shared._log.warning("Agent continuation %s rejected: %s", execution.execution_id, exc)
                raise shared.AgentDriverError(exc.code, str(exc)) from exc
        if activation is not None and activation.checkpoint is not None and self.activation_observer is not None:
            self.activation_observer(activation)
        cancel_event = shared.threading.Event()
        self._control_service().attempts.set_process_owner(
            attempt.attempt_id, generation=attempt.generation, active=True,
        )
        # Activation is often initiated by a short-lived transport loop.  An
        # execution owner must not inherit that loop's cancellation lifetime,
        # including the initial Job attempt.  The driver owns a thread-backed
        # completion future for both initial and resumed activations.
        task: shared.Any = shared._ThreadResultFuture()
        handle = shared.AgentDriverHandle(
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
                raise shared.AgentDriverError("owner_exists", "execution already has a live Agent owner")
            self._handles[key] = handle
            self._continuation_start_gates[key] = shared.threading.Event()
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
                # Validate ownership before executing any user work.
                self._control_service().attempts.ensure_held(
                    attempt.attempt_id, generation=attempt.generation,
                )
                result = shared.asyncio.run(
                    self._run_attempt(
                        attempt, request, cancel_event,
                        continuation=continuation,
                        steer_inputs=steer_inputs,
                    )
                )
            except BaseException as exc:
                self._recover_owner_loss(attempt)
                task.set_exception(exc)
            else:
                task.set_result(result)

        shared.threading.Thread(
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


    def activation_committed(self, binding: DriverBinding[shared.AgentDriverHandle]) -> None:
        """Release a driver-owned producer after registry fencing commits."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.get(key)
            if gate is None:
                return
            self._continuation_committed.add(key)
            gate.set()


    def activation_aborted(self, binding: DriverBinding[shared.AgentDriverHandle]) -> None:
        """Discard a producer whose registry bind lost a fence."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.pop(key, None)
            self._continuation_committed.discard(key)
        if gate is not None:
            gate.set()


    def _tell_function_process(self, handle: shared.AgentDriverHandle, command: str) -> None:
        """A function subprocess learns of a stop from a line in its log."""
        from openprogram.runtime.conversation.process_runner import is_subprocess_alive

        record = self.executions.get_execution(handle.execution_id)
        if record is not None and is_subprocess_alive(record.session_id, execution_id=handle.execution_id):
            self.executions.function_log(handle.execution_id).request(command, handle.generation)

    async def request_pause(
        self, handle: shared.AgentDriverHandle, command_id: str
    ) -> DriverAck:
        self._require_live(handle)
        # Pause is cooperative: the loop observes the durable APPLYING
        # command at its next declared provider/tool boundary.  ACK only
        # confirms delivery to this fenced owner; it never fabricates a
        # checkpoint or changes command state.
        self._tell_function_process(handle, "pause")
        return DriverAck(command_id=command_id, attempt_id=handle.attempt_id)


    async def request_cancel(
        self, handle: shared.AgentDriverHandle, command_id: str
    ) -> DriverAck:
        self._require_live(handle)
        # The canonical control service has already fenced and dispatched this
        # exact command_id to this owner. Retain it even if a diagnostic read
        # of the command row is temporarily unavailable; finish/repair must
        # carry the same identity to the durable command transition.
        with self._handles_lock:
            self._cancel_commands[self._key(handle)] = command_id
        from openprogram.runtime.execution.run_control.wakeups import set_and_fire
        set_and_fire(handle.cancel_event)
        self._tell_function_process(handle, "cancel")
        return DriverAck(command_id=command_id, attempt_id=handle.attempt_id)


    async def inspect(self, handle: shared.AgentDriverHandle) -> RuntimeSnapshot:
        self._require_live(handle)
        return RuntimeSnapshot(
            attempt_id=handle.attempt_id,
            state_schema_version=0,
            safe_point_kind=None,
            state={"done": handle.done.done()},
        )


    async def terminate(
        self, handle: shared.AgentDriverHandle, reason: str
    ) -> TerminationReceipt:
        self._require_live(handle)
        from openprogram.runtime.execution.run_control.wakeups import set_and_fire
        set_and_fire(handle.cancel_event)
        killed = False
        try:
            from openprogram.runtime.conversation.process_runner import kill_active_subprocess

            killed = kill_active_subprocess(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            shared._log.exception(
                "failed to terminate Agent subprocess for %s",
                handle.execution_id,
            )
        try:
            from openprogram.runtime.conversation.run_control import kill_active_runtime

            kill_active_runtime(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            shared._log.exception(
                "failed to terminate Agent runtime for %s",
                handle.execution_id,
            )
        return TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=killed or handle.done.done(),
            reason=reason,
        )


    def fail_admission(
        self, admission: shared.CanonicalAgentAdmission, *, reason_code: str,
        target: ExecutionStatus = ExecutionStatus.FAILED,
        failure_text: str | None = None,
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
                owner_id=f"agent-failure-{shared.uuid.uuid4().hex}",
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
                failure_text=failure_text,
            )
        except Exception:
            # The durable record may already have been handled by another
            # owner or recovery pass. Never replace that state from a
            # transport startup exception.
            return

