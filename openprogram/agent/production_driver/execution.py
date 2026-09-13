"""AgentProductionDriver execution operations."""
from __future__ import annotations
from . import shared


class ExecutionOperations:
    async def _run_attempt(
        self,
        attempt: shared.AttemptRecord,
        request: shared.Any,
        cancel_event: shared.threading.Event,
        *,
        continuation: shared.AgentContinuation | None = None,
        steer_inputs: tuple[shared.Mapping[str, shared.Any], ...] = (),
    ) -> shared.Any:
        try:
            result = await shared.asyncio.to_thread(
                self._run_turn,
                attempt,
                request,
                cancel_event,
                continuation,
                steer_inputs,
            )
        except shared.asyncio.CancelledError:
            if cancel_event.is_set():
                self._finish_attempt(attempt, None, cancel_event)
            else:
                self._recover_owner_loss(attempt)
            return None
        except Exception as exc:
            from openprogram.agent.run_control import is_worker_stopping
            if is_worker_stopping() and not cancel_event.is_set():
                self._recover_owner_loss(attempt)
                return None
            if cancel_event.is_set():
                self._finish_attempt(attempt, None, cancel_event)
            else:
                shared._log.exception("Agent execution %s failed in its runner", attempt.execution_id)
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
        # A settings update may race wait publication. Recheck only after
        # _run_turn has released the old runtime and cancellation token.
        from openprogram.agent.permissions import reconcile_permission_waits
        await reconcile_permission_waits(request.session_id, service=self._control_service())
        return result


    def _run_turn(
        self,
        attempt: shared.AttemptRecord,
        request: shared.Any,
        cancel_event: shared.threading.Event,
        continuation: shared.AgentContinuation | None = None,
        steer_inputs: tuple[shared.Mapping[str, shared.Any], ...] = (),
    ) -> shared.Any:
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
            raise shared.AgentDriverError("owner_conflict", "Agent execution already has a live runtime")
        if not isinstance(request, shared.ForcedToolActivation):
            setattr(request, "_execution_revision_id", attempt.execution_id)
            execution = self.executions.get_execution(attempt.execution_id)
            if execution is not None:
                setattr(request, "_execution_revision_id", execution.revision_id)
        session_token = set_current_session_id(request.session_id)
        execution_token = set_current_execution_id(attempt.execution_id)
        bound = current_token(request.session_id, execution_id=attempt.execution_id)
        token_token = _current_token.set(bound) if bound is not None else None
        job_tokens: list[shared.Any] = []
        worktree_token = None
        try:
            job_context = getattr(request, "_job_context", None)
            if isinstance(job_context, shared.Mapping):
                from openprogram.agent.job.runner import (
                    _current_job_governance,
                    _current_job_id,
                    _current_job_runner,
                    runner_for_execution_store,
                )

                job_runner = runner_for_execution_store(self.executions)
                if job_runner is None:
                    raise shared.AgentDriverError(
                        "job_runner_unavailable",
                        "canonical Job owner has no resource runner",
                    )
                job = job_runner._canonical_job(attempt.execution_id)
                if job is None:
                    raise shared.AgentDriverError(
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
                if isinstance(chain, shared.Mapping):
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
                        raise shared.AgentDriverError(
                            "worktree_not_found",
                            f"Job worktree is unavailable: {worktree_id}",
                        )
                    worktree_token = set_worktree(worktree.worktree_path)
            # ActivationInput recursively freezes payloads; copy nested mappings
            # without deepcopy, which cannot pickle MappingProxyType values.
            steer_queue = [shared._json_safe(item) for item in steer_inputs]
            steer_consumed_ids: set[str] = set()

            @shared.contextmanager
            def persist_steer(command_id: str, message_id: str):
                # Serialize delivery with terminal closure. The session write is
                # idempotent; closure can recover it if this SQL commit fails.
                with self.executions._transaction() as connection:
                    attempts = self._control_service().attempts
                    owner = attempts._require(connection, attempt.attempt_id)
                    execution = self.executions._require_execution(connection, attempt.execution_id)
                    attempts._validate_generation(owner, attempt.generation)
                    attempts._validate_lease(owner, attempts._clock())
                    attempts._validate_owner(execution, owner, execution.status_version)
                    if owner.status is not shared.AttemptStatus.ACTIVE:
                        raise shared.AgentDriverError("stale_owner", "steering requires an active attempt")
                    command = self.executions._get_command(connection, command_id)
                    if (
                        command is None
                        or command.execution_id != attempt.execution_id
                        or command.kind is not shared.CommandKind.STEER
                        or command.status not in {
                            shared.CommandStatus.ACCEPTED, shared.CommandStatus.APPLYING, shared.CommandStatus.APPLIED,
                        }
                    ):
                        raise shared.AgentDriverError("steer_closed", "steering delivery is no longer pending")
                    if command.status is shared.CommandStatus.ACCEPTED:
                        command = self.executions._transition_command(
                            connection, command_id,
                            expected_status=shared.CommandStatus.ACCEPTED,
                            target=shared.CommandStatus.APPLYING,
                        )
                    yield
                    if command.status is shared.CommandStatus.APPLYING:
                        self.executions._transition_command(
                            connection, command_id,
                            expected_status=shared.CommandStatus.APPLYING,
                            target=shared.CommandStatus.APPLIED,
                            receipt={"user_message_id": message_id},
                        )
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
                    "persist_steer": persist_steer,
                }
                if getattr(request, "_job_context", None) is not None:
                    execution_context["job_context"] = shared.copy.deepcopy(request._job_context)
                result = process_agent_continuation(
                    continuation,
                    on_event=self.event_sink,
                    cancel_event=cancel_event,
                    execution_context=execution_context,
                )
            elif isinstance(request, shared.ForcedToolActivation):
                from openprogram.system_access import access_manifest_for_tool
                manifest = access_manifest_for_tool(
                    request.tool_name, dict(request.tool_input),
                )
                if manifest is not None:
                    self._open_forced_system_access_wait(attempt, request, manifest)
                    return shared._SafePointHandoff()
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
                if isinstance(result, shared.Mapping) and result.get("function_suspended"):
                    from openprogram.execution.checkpoints import CheckpointFragment
                    from openprogram.agentic_programming.continuation import default_policy
                    from openprogram.execution.restart import window_seconds
                    service = self._control_service()
                    current = self.executions.get_execution(attempt.execution_id)
                    commands = self.executions.list_commands(attempt.execution_id, kinds=(shared.CommandKind.PAUSE,), statuses=(shared.CommandStatus.APPLYING,))
                    if not commands:
                        raise shared.AgentDriverError("pause_command_missing", "Function suspension has no pending pause")
                    service.arrive_safe_point(
                        attempt_id=attempt.attempt_id, generation=attempt.generation,
                        command_id=commands[0].command_id, expected_execution_version=current.status_version,
                        fragment=CheckpointFragment(
                            safe_point_kind="function.step.after",
                            frontier=({"kind": "function.step.after", "call_key": result["call_key"]},),
                            state_refs={"function": {"version": 1, "call_key": result["call_key"], "policy": default_policy(self.executions, attempt.execution_id)}, "restart_window_seconds": window_seconds()},
                        ),
                    )
                    return shared._SafePointHandoff()
            else:
                runner_kwargs = {
                    "request": request,
                    "cancel_event": cancel_event,
                }
                try:
                    parameters = shared.inspect.signature(self.turn_runner).parameters
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
                            "restart_initial": attempt.generation > 1,
                            "steer_inputs": steer_queue,
                            "steer_consumed_ids": steer_consumed_ids,
                            "persist_steer": persist_steer,
                        }
                        if getattr(request, "_job_context", None) is not None:
                            runner_kwargs["execution_context"]["job_context"] = shared.copy.deepcopy(request._job_context)
                except (TypeError, ValueError):
                    pass
                result = self.turn_runner(**runner_kwargs)
            if shared.inspect.isawaitable(result):
                raise shared.AgentDriverError("invalid_runner", "Agent turn runner must be synchronous")
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


    def _open_forced_system_access_wait(
        self,
        attempt: shared.AttemptRecord,
        request: shared.ForcedToolActivation,
        manifest: shared.Mapping[str, shared.Any],
    ) -> None:
        """Suspend a forced local GUI entry before its subprocess is spawned."""
        from openprogram.execution.checkpoints import CheckpointFragment

        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise shared.AgentDriverError(
                "execution_not_found",
                "execution disappeared before system access wait",
            )
        digest = shared.hashlib.sha256(shared.canonical_json_bytes({
            "execution_id": attempt.execution_id,
            "generation": attempt.generation,
            "tool_name": request.tool_name,
            "tool_input": dict(request.tool_input),
        })).hexdigest()[:32]
        wait_id = f"wait_{digest}"
        request_data = {
            "prompt": str(manifest.get("prompt") or ""),
            "options": list(manifest.get("options") or ()),
            "multi": bool(manifest.get("multi", False)),
            "allow_custom": bool(manifest.get("allow_custom", False)),
            "detail": str(manifest.get("detail") or ""),
            "schema": dict(manifest.get("schema") or {}),
            "questions": list(manifest.get("questions") or ()),
            **dict(manifest.get("request_metadata") or {}),
        }
        suspension = self._control_service().open_wait_at_safe_point(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            expected_version=execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="agent.wait.before_tool",
                frontier=({"step_id": "forced_tool.before", "phase": "before_tool"},),
                state_refs={"forced_tool": {
                    "version": 1,
                    "tool_name": request.tool_name,
                    "tool_input": dict(request.tool_input),
                }},
            ),
            kind="system_access",
            request=request_data,
            policy_snapshot=dict(manifest.get("policy_snapshot") or {
                "version": 1, "kind": "system_access", "on_grant": "continue",
            }),
            expires_at=0,
            wait_id=wait_id,
        )
        try:
            from openprogram.events import emit_ws_frame
            emit_ws_frame({"type": "system_access.waiting", "data": {
                "id": suspension.wait.wait_id,
                "wait_id": suspension.wait.wait_id,
                "kind": "system_access",
                "session_id": request.session_id,
                "execution_id": attempt.execution_id,
                "required_capabilities": list(request_data.get("required_capabilities", [])),
                "capabilities": list(request_data.get("capabilities", [])),
                "wait_generation": suspension.wait.claim_generation,
                "expected_version": suspension.execution.status_version,
                "expires_at": 0,
                "reason_code": "system_access_required",
                "live": True,
            }})
        except Exception:
            shared._log.debug("failed to publish forced system access wait", exc_info=True)

