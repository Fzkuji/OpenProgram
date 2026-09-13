"""AgentProductionDriver activation operations."""
from __future__ import annotations
from . import shared


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
        self, record: shared.Any, activation: shared.ActivationInput | None,
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
            marker = activation.checkpoint.state_refs.get("forced_tool")
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
    def capabilities_for_payload(payload: shared.Mapping[str, shared.Any]) -> shared.CapabilitySet:
        """Return the admitted capability contract, never a transport guess."""
        if not isinstance(payload, shared.Mapping):
            raise shared.AgentDriverError("invalid_input", "Agent admission input must be an object")
        if payload.get("kind") == "job_agent":
            # Job input has its own strict envelope.  A Job is still driven by
            # the same Agent loop, so it exposes the same provider/tool safe
            # points and durable steering contract as ordinary chat.
            from openprogram.agent.job.input import JobAgentInputError, JobAgentInputV1

            try:
                JobAgentInputV1.parse(payload)
            except JobAgentInputError as exc:
                raise shared.AgentDriverError("invalid_job_input", str(exc)) from exc
            return shared.CapabilitySet(
                pause=True,
                step=True,
                steer=True,
                fork=True,
                retry=True,
                safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
                state_schema_version=shared.AGENT_CHECKPOINT_SCHEMA_VERSION,
            )
        envelope = shared.normalize_agent_turn_payload(payload)
        if envelope["kind"] != "chat":
            tool_name = str(envelope.get("tool_name") or "")
            tool_input = envelope.get("tool_input")
            surface = str(tool_input.get("surface") or "").strip().lower() if isinstance(tool_input, shared.Mapping) else ""
            desktop_wait = (
                tool_name == "gui_agent"
                and surface in {"", "desktop"}
                and isinstance(tool_input, shared.Mapping)
                and not tool_input.get("vm_url")
                and not (not surface and tool_input.get("backend"))
            )
            if not desktop_wait:
                return shared.CapabilitySet()
            return shared.CapabilitySet(
                pause=True,
                safe_point_kinds=("agent.wait.before_tool",),
                state_schema_version=shared.AGENT_CHECKPOINT_SCHEMA_VERSION,
            )
        request = envelope["request"]
        text = str(request.get("user_text") or "").lstrip()
        if (
            request.get("interaction") in {"spawn", "merge"}
            or text.startswith(("/forced_tool", "/spawn", "/merge"))
        ):
            return shared.CapabilitySet()
        return shared.CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
            state_schema_version=shared.AGENT_CHECKPOINT_SCHEMA_VERSION,
        )


    def capabilities(self) -> shared.CapabilitySet:
        """Default driver capability used only by direct internal callers.

        Admission uses ``capabilities_for_payload`` so a forced tool cannot
        inherit ordinary-chat controls from a mutable driver instance.
        """
        return shared.CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            fork=True,
            retry=True,
            safe_point_kinds=shared.AGENT_SAFE_POINT_KINDS,
            state_schema_version=shared.AGENT_CHECKPOINT_SCHEMA_VERSION,
        )


    async def activate(
        self,
        attempt: shared.AttemptRecord,
        activation: shared.ActivationInput | None,
    ) -> shared.DriverBinding[shared.AgentDriverHandle]:
        if self.executions is None:
            raise shared.AgentDriverError("store_required", "Agent activation requires an execution store")
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise shared.AgentDriverError("execution_not_found", f"execution not found: {attempt.execution_id}")
        if (
            attempt.status is not shared.AttemptStatus.ACTIVE
            or execution.status is not shared.ExecutionStatus.RUNNING
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
                continuation = shared.AgentContinuation.from_checkpoint(
                    store=self.executions,
                    checkpoint=activation.checkpoint,
                    request=request,
                )
                if (execution.parent_execution_id is not None
                        and activation.checkpoint.execution_id != execution.execution_id):
                    with self.executions._connect() as connection:
                        connection.execute("BEGIN")
                        branch = shared.RuntimeControlService._agent_branch_checkpoint_is_valid(
                            self.executions, connection, execution, activation.checkpoint,
                        )
                    if not branch:
                        raise shared.AgentDriverError("invalid_checkpoint", "Agent branch source checkpoint is invalid")
                    if record.assistant_message_id != f"{execution.execution_id}_reply":
                        raise shared.AgentDriverError("checkpoint_schema_invalid", "Agent branch assistant anchor is invalid")
                    branch_payload = shared.copy.deepcopy(dict(continuation.state.payload))
                    branch_payload["turn"]["assistant_message_id"] = record.assistant_message_id
                    branch_state = shared.replace(continuation.state, payload=branch_payload)
                    branch_state.validate()
                    branch_snapshot = shared.copy.deepcopy(dict(continuation.resolved_snapshot))
                    # The validated manifest permits only this revision identity
                    # change; model, tools, prompt and authority remain exact.
                    branch_snapshot["request_semantics"]["_execution_revision_id"] = execution.revision_id
                    continuation = shared.replace(
                        continuation, state=branch_state, resolved_snapshot=branch_snapshot,
                    )
                    from openprogram.agent.session_db import default_db
                    from openprogram.context.nodes import Call, ROLE_LLM
                    from openprogram.store import SessionNodeWriter
                    db = default_db()
                    if not db.message_exists(record.session_id, record.assistant_message_id):
                        SessionNodeWriter(db, record.session_id, advance_head=False).append(Call(
                            id=record.assistant_message_id, role=ROLE_LLM, output="",
                            predecessor=record.user_message_id, created_at=shared.time.time(),
                            metadata={"agent_id": request.agent_id, "execution_id": execution.execution_id},
                        ))
                if (
                    record.user_message_id != continuation.state.payload["turn"]["user_message_id"]
                    or record.assistant_message_id != continuation.assistant_message_id
                ):
                    raise shared.AgentDriverError(
                        "checkpoint_schema_invalid",
                        "Agent checkpoint branch anchors differ from immutable admission input",
                    )
                from openprogram.agent.session_db import default_db
                from openprogram.store import SessionNodeWriter
                assistant_node = SessionNodeWriter(default_db(), record.session_id).load().nodes.get(record.assistant_message_id)
                if assistant_node and assistant_node.predecessor != record.user_message_id:
                    request._steering_tail_id = assistant_node.predecessor
                from openprogram.agent.dispatcher.loop_runner import resolve_agent_runtime
                from openprogram.agent.internals._workdir import runtime_location_for
                from openprogram.worktree.context import reset_worktree, set_worktree
                _location = runtime_location_for(request.session_id, use_context=False)
                _workdir_token = set_worktree(_location["workdir"])
                try:
                    _profile, _tools, _recordable, _prompt, _model, _contract = resolve_agent_runtime(
                        request,
                        assistant_msg_id=continuation.assistant_message_id,
                        saved_runtime_contract=continuation.resolved_snapshot,
                    )
                finally:
                    reset_worktree(_workdir_token)
                shared.validate_runtime_contract(continuation.resolved_snapshot, _contract)
            except shared.AgentCheckpointError as exc:
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
                # Validate ownership before executing any user work, then
                # retain the exact fenced lease independently of transport
                # event loops, including bounded terminal-write retries.
                self._control_service().attempts.heartbeat(
                    attempt.attempt_id, generation=attempt.generation,
                    ttl_seconds=shared.AGENT_LEASE_SECONDS,
                )
                shared.threading.Thread(
                    target=self._maintain_owner,
                    args=(attempt, handle),
                    daemon=True,
                    name=f"openprogram-agent-lease-{attempt.attempt_id}",
                ).start()
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
        return shared.DriverBinding(
            execution_id=handle.execution_id,
            attempt_id=handle.attempt_id,
            generation=handle.generation,
            driver=self,
            handle=handle,
        )


    def _maintain_owner(self, attempt: shared.AttemptRecord, handle: shared.AgentDriverHandle) -> None:
        """Renew a live producer's lease; never renew a replacement owner."""
        wake = shared.threading.Event()
        handle.done.add_done_callback(lambda _done: wake.set())
        key = self._key(handle)
        interval = shared.AGENT_LEASE_SECONDS / 3
        while True:
            wake.wait(interval)
            wake.clear()
            with self._handles_lock:
                pending = key in self._pending_finishes
                stalled = key in self._finish_repair_stalled
            if handle.done.done() and (not pending or stalled):
                return
            service = self._control_service()
            try:
                service.attempts.heartbeat(
                    attempt.attempt_id, generation=attempt.generation,
                    ttl_seconds=shared.AGENT_LEASE_SECONDS,
                )
            except Exception:
                # Signal this producer only. Looking up a physical process by
                # execution_id could kill a replacement after a concurrent
                # handoff. In-flight external operations remain cooperative.
                handle.cancel_event.set()
                try:
                    current = service.attempts.get(attempt.attempt_id)
                    if current is not None and current.status is shared.AttemptStatus.ENDED:
                        return
                except Exception:
                    pass
                self._recover_owner_loss(attempt)
                return
            if handle.done.done():
                # Finish retries are bounded and can settle before the next
                # normal heartbeat. Do not leave an idle lease thread alive.
                interval = min(shared.AGENT_LEASE_SECONDS / 3, 0.1)


    def activation_committed(self, binding: shared.DriverBinding[shared.AgentDriverHandle]) -> None:
        """Release a driver-owned producer after registry fencing commits."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.get(key)
            if gate is None:
                return
            self._continuation_committed.add(key)
            gate.set()


    def activation_aborted(self, binding: shared.DriverBinding[shared.AgentDriverHandle]) -> None:
        """Discard a producer whose registry bind lost a fence."""
        key = self._key(binding.handle)
        with self._handles_lock:
            gate = self._continuation_start_gates.pop(key, None)
            self._continuation_committed.discard(key)
        if gate is not None:
            gate.set()


    async def request_pause(
        self, handle: shared.AgentDriverHandle, command_id: str
    ) -> shared.DriverAck:
        self._require_live(handle)
        # Pause is cooperative: the loop observes the durable APPLYING
        # command at its next declared provider/tool boundary.  ACK only
        # confirms delivery to this fenced owner; it never fabricates a
        # checkpoint or changes command state.
        return shared.DriverAck(command_id=command_id, attempt_id=handle.attempt_id)


    async def request_cancel(
        self, handle: shared.AgentDriverHandle, command_id: str
    ) -> shared.DriverAck:
        self._require_live(handle)
        # The canonical control service has already fenced and dispatched this
        # exact command_id to this owner. Retain it even if a diagnostic read
        # of the command row is temporarily unavailable; finish/repair must
        # carry the same identity to the durable command transition.
        with self._handles_lock:
            self._cancel_commands[self._key(handle)] = command_id
        handle.cancel_event.set()
        return shared.DriverAck(command_id=command_id, attempt_id=handle.attempt_id)


    async def inspect(self, handle: shared.AgentDriverHandle) -> shared.RuntimeSnapshot:
        self._require_live(handle)
        return shared.RuntimeSnapshot(
            attempt_id=handle.attempt_id,
            state_schema_version=0,
            safe_point_kind=None,
            state={"done": handle.done.done()},
        )


    async def terminate(
        self, handle: shared.AgentDriverHandle, reason: str
    ) -> shared.TerminationReceipt:
        self._require_live(handle)
        handle.cancel_event.set()
        killed = False
        try:
            from openprogram.agent.process_runner import kill_active_subprocess

            killed = kill_active_subprocess(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            shared._log.exception(
                "failed to terminate Agent subprocess for %s",
                handle.execution_id,
            )
        try:
            from openprogram.agent.run_control import kill_active_runtime

            kill_active_runtime(
                handle.session_id, execution_id=handle.execution_id,
            )
        except Exception:
            shared._log.exception(
                "failed to terminate Agent runtime for %s",
                handle.execution_id,
            )
        return shared.TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=killed or handle.done.done(),
            reason=reason,
        )


    def fail_admission(
        self, admission: shared.CanonicalAgentAdmission, *, reason_code: str,
        target: shared.ExecutionStatus = shared.ExecutionStatus.FAILED,
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
                ttl_seconds=shared.AGENT_LEASE_SECONDS,
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
                outcome="cancelled" if target is shared.ExecutionStatus.CANCELLED else "failed",
                reason_code=reason_code,
            )
        except Exception:
            # The durable record may already have been handled by another
            # owner or recovery pass. Never replace that state from a
            # transport startup exception.
            return

