"""What each boundary of a running Agent turn writes and checks.

A step adds lines to the turn's journal and reads the open commands; it writes
nothing else. A checkpoint is published only where the turn stops: at a pause,
a step, a wait, or a suspended function.
"""
from __future__ import annotations
from openprogram.runtime.conversation.checkpoint import AgentContinuation
import time
from openprogram.runtime.conversation.production_driver import shared
from openprogram.runtime.execution.agent_journal import AgentJournal, manifest_fields
from openprogram.runtime.execution.attempts import AttemptRecord
from openprogram.runtime.execution.model import CommandKind, CommandStatus
from openprogram.runtime.execution.model import ExecutionStatus


def _digest(*parts: str) -> str:
    """Hash identity parts into a stable action identifier."""
    value = "\x1f".join(parts).encode("utf-8")
    return shared.hashlib.sha256(value).hexdigest()


def _interrupt_cancelled() -> None:
    """Interrupt the Agent loop because the execution is cancelling."""
    from openprogram.models.providers.utils.failures.errors import ExecInterrupt
    raise ExecInterrupt("cancelled")


class _AgentSafePointHook:
    """One attempt's journal plus the callable the Agent loop invokes."""

    def __init__(
        self,
        driver: shared.Any,
        attempt: AttemptRecord,
        request: shared.Any,
        cancel_event: shared.threading.Event,
        continuation: AgentContinuation | None,
        steer_queue: list[dict[str, shared.Any]] | None,
        steer_consumed_ids: set[str] | None,
    ) -> None:
        self.driver = driver
        self.attempt = attempt
        self.request = request
        self.cancel_event = cancel_event
        self.continuation = continuation
        self.steer_queue = steer_queue
        self.steer_consumed_ids = steer_consumed_ids
        self.journal = self._open_journal(continuation)
        self.tool_arguments: dict[str, shared.Mapping[str, shared.Any]] = {}

    def _open_journal(self, continuation: AgentContinuation | None) -> AgentJournal:
        session_id = self.request.session_id
        execution_id = self.attempt.execution_id
        if continuation is None:
            return AgentJournal(session_id, execution_id)
        pointer = continuation.pointer
        if pointer.get("execution_id") == execution_id:
            journal = AgentJournal(
                session_id, execution_id, state=continuation.journal,
                position=int(pointer.get("position") or 0), source=pointer.get("source"),
            )
        else:
            journal = AgentJournal(session_id, execution_id, state=continuation.journal, source=pointer)
        interrupted = continuation.interrupted_result
        if interrupted is not None:
            journal.record_tool_result(
                interrupted.tool_call_id, interrupted.model_dump(mode="json"), is_error=True,
                outcome="interrupted", repeat_failures=continuation.repeat_failures,
                loaded_deferred_tools=continuation.loaded_deferred_tools,
            )
        return journal

    @property
    def provider_action_id(self) -> str:
        decision = self.journal.state.last
        return decision.action_id if decision is not None else ""

    def record_steer(self, command_id: str, message_id: str, message: shared.Mapping[str, shared.Any]) -> None:
        """Note delivered steering so a resumed turn shows it to the model."""
        self.journal.record_steer(command_id, message_id, message)

    @staticmethod
    def _current_command(service, execution_id: str):
        """Return the open control command that wins at this safe point."""
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

    def _turn(self, payload: shared.Mapping[str, shared.Any]) -> dict[str, str]:
        """The turn's message identities."""
        if self.journal.state.turn:
            return dict(self.journal.state.turn)
        execution = self.driver._control_service().executions.get_execution(self.attempt.execution_id)
        if execution is None:
            raise shared.AgentDriverError("execution_not_found", "execution disappeared at safe point")
        raw = payload.get("turn") if isinstance(payload.get("turn"), shared.Mapping) else {}
        user_id = str(
            raw.get("user_message_id") or getattr(execution, "user_message_id", None)
            or getattr(self.request, "user_msg_id", "") or "durable-user"
        )
        return {
            "user_message_id": user_id,
            "assistant_message_id": str(
                raw.get("assistant_message_id") or getattr(execution, "assistant_message_id", None)
                or f"{user_id}_reply"
            ),
            "base_history_head_id": str(raw.get("base_history_head_id") or user_id),
        }

    def _fragment(self, service, *, waiting_on: str | None = None, managed_action=None):
        """The small checkpoint manifest naming this journal position."""
        from openprogram.runtime.execution.recovery.checkpoints import CheckpointFragment
        from openprogram.runtime.execution.recovery.restart import window_seconds

        kind, frontier, refs = manifest_fields(
            self.journal.state, self.journal.pointer(), waiting_on=waiting_on,
            restart_window_seconds=window_seconds(),
        )
        pending = tuple(
            command.command_id
            for command in service.executions.list_commands(self.attempt.execution_id)
            if command.status not in {CommandStatus.APPLIED, CommandStatus.REJECTED}
            and command.command_id not in (self.steer_consumed_ids or ())
        )
        return CheckpointFragment(
            safe_point_kind=kind, frontier=frontier, state_refs=refs,
            pending_command_ids=pending, managed_action=managed_action, leaves_steering_open=True,
        )

    # -- Pausing: stop before new work starts ---------------------------

    def _pause_before_dispatch(self, service):
        """Consume pause at the last completed step, without starting work."""
        attempt = self.attempt
        execution = service.executions.get_execution(attempt.execution_id)
        if execution.status is ExecutionStatus.CANCELLING or self.cancel_event.is_set():
            _interrupt_cancelled()
        if execution.current_attempt_id != attempt.attempt_id:
            raise shared.AgentDriverError("stale_owner", "this attempt no longer owns the execution")
        if execution.status is not ExecutionStatus.PAUSING:
            return False
        command = self._current_command(service, attempt.execution_id)
        if command is None or command.kind is not CommandKind.PAUSE:
            return False
        if command.status is CommandStatus.ACCEPTED:
            service.executions.transition_command(
                command.command_id, expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
        if not self.journal.state.has_decision:
            service.pause_before_first_effect(
                attempt_id=attempt.attempt_id, generation=attempt.generation,
                command_id=command.command_id,
            )
            return True
        service.arrive_safe_point(
            attempt_id=attempt.attempt_id, generation=attempt.generation,
            command_id=command.command_id,
            expected_execution_version=execution.status_version, fragment=self._fragment(service),
        )
        return True

    # -- Running: observation --------------------------------------------

    def _observe_provider_poll(self, service, payload) -> bool:
        """Raise on cancel and flag a pending steer while the provider streams."""
        attempt = self.attempt
        # Observation only: application still occurs at provider.after,
        # after the producer closes and its partial response is saved.
        current = service.executions.get_execution(attempt.execution_id)
        if self.cancel_event.is_set() or (current and current.status is ExecutionStatus.CANCELLING):
            _interrupt_cancelled()
        command = self._current_command(service, attempt.execution_id)
        payload["steer_pending"] = bool(
            current and current.current_attempt_id == attempt.attempt_id
            and current.status is ExecutionStatus.RUNNING
            and command is not None and command.kind is CommandKind.STEER
        )
        return False

    @staticmethod
    def _recheck_system_access(payload) -> None:
        """Re-probe system access so a granted permission opens no wait."""
        from openprogram.host.system_access import access_manifest_for_tool
        # The manifest was captured while building the tool list;
        # re-probe at the canonical boundary before opening or
        # reusing a wait so a grant race does not suspend a task
        # after access is already available. When access has
        # arrived there is no wait, and the tool goes on to the
        # ordinary dispatch bookkeeping below (tool.started looks
        # its intent up there).
        payload["pre_wait"] = access_manifest_for_tool(
            str(payload.get("tool_name") or ""),
            dict(payload.get("arguments") or {}),
        )

    # -- Waiting: durable question or approval before a tool --------------

    def _wait_identity(self, payload, pre_wait):
        """Derive the wait's kind and stable id, and look up an existing record."""
        from openprogram.runtime.execution.waits import DurableWaitStore, WaitStatus

        attempt = self.attempt
        tool_call_id = str(payload.get("tool_call_id") or "")
        if not self.provider_action_id or not tool_call_id:
            raise shared.AgentDriverError("checkpoint_schema_invalid", "wait has no stable provider or tool identity")
        wait_kind = str(pre_wait.get("kind") or "")
        if wait_kind not in {"approval", "ask", "ask_many", "confirm", "form", "system_access"}:
            raise shared.AgentDriverError("invalid_wait", "wait kind is not supported at an Agent tool boundary")
        wait_id_parts = [
            attempt.execution_id, self.provider_action_id, tool_call_id, wait_kind,
        ]
        if wait_kind == "system_access":
            wait_id_parts.append(str(attempt.generation))
        wait_id = "wait_" + _digest(*wait_id_parts)[:32]
        existing = DurableWaitStore(self.driver.executions).get_wait(wait_id)
        if wait_kind == "system_access" and existing is not None and existing.status is WaitStatus.RESOLVED:
            # A resolved receipt is tied to an earlier executor
            # report. Never reuse it after a revocation race.
            wait_id = "wait_" + _digest(
                attempt.execution_id, self.provider_action_id, tool_call_id,
                wait_kind, str(attempt.generation), "recheck",
            )[:32]
            existing = DurableWaitStore(self.driver.executions).get_wait(wait_id)
        return wait_kind, wait_id, existing

    def _wait_before_tool(self, service, payload) -> bool:
        """Open or reuse the tool's durable wait; True suspends the turn."""
        from openprogram.runtime.execution.waits import WaitStatus

        pre_wait = dict(payload["pre_wait"])
        wait_kind, wait_id, existing = self._wait_identity(payload, pre_wait)
        if existing is not None:
            if existing.status is WaitStatus.RESOLVED or (
                existing.status is WaitStatus.DECLINED
                and existing.policy_snapshot.get("on_decline") == "continue"
            ):
                # The tool reads the outcome; a declined approval
                # reports the refusal instead of running.
                payload["preapproved_wait_id"] = wait_id
                return False
            # Other outcomes settle the execution before a
            # replacement attempt can reach this boundary. Do not
            # dispatch the protected tool from an unresolved record.
            return True
        self._open_wait(service, payload, pre_wait, wait_kind, wait_id)
        return True

    def _open_wait(self, service, payload, pre_wait, wait_kind: str, wait_id: str) -> None:
        """Checkpoint at the wait, open it durably and announce it."""
        from openprogram.runtime.execution.recovery.checkpoints import CheckpointFragment

        attempt = self.attempt
        current = service.executions.get_execution(attempt.execution_id)
        if current is None:
            raise shared.AgentDriverError("execution_not_found", "execution disappeared before wait")
        request_metadata = pre_wait.get("request_metadata", {})
        if not isinstance(request_metadata, shared.Mapping):
            raise shared.AgentDriverError("invalid_wait", "wait request metadata is invalid")
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
            raise shared.AgentDriverError("invalid_wait", "wait metadata cannot replace presentation fields")
        wait_request.update(dict(request_metadata))
        timeout = pre_wait.get("timeout", 300.0)
        if timeout is not None and (type(timeout) not in {int, float} or timeout <= 0):
            raise shared.AgentDriverError("invalid_wait", "approval wait timeout is invalid")
        policy = pre_wait.get("policy_snapshot")
        if not isinstance(policy, shared.Mapping):
            raise shared.AgentDriverError("invalid_wait_policy", "approval wait policy is invalid")
        suspension = service.open_wait_at_safe_point(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            expected_version=current.status_version,
            fragment=self._fragment(service, waiting_on=str(payload.get("tool_call_id") or "")),
            kind=wait_kind, request=wait_request,
            policy_snapshot=dict(policy), expires_at=0 if timeout is None else time.time() + float(timeout),
            wait_id=wait_id,
        )
        self._publish_wait(suspension, wait_kind, wait_request)

    def _publish_wait(self, suspension, wait_kind: str, wait_request) -> None:
        """Send the live question or system-access frame for an opened wait."""
        try:
            sink = self.driver.event_sink
            if sink is None:
                # Durable continuations can outlive the original
                # chat transport. Their next wait still needs a
                # live notification on the shared event stream.
                from openprogram.runtime.events import emit_ws_frame
                sink = emit_ws_frame
            frame_type = "system_access.waiting" if wait_kind == "system_access" else "question.asked"
            from openprogram.runtime.execution.waits import question_frame_data
            # A foreground helper forked into another conversation asks in
            # its caller's (approval._asker_metadata).
            shown_in = (wait_request.get("asked_in_session")
                        if isinstance(wait_request, shared.Mapping) else None)
            data = question_frame_data(
                suspension.wait, session_id=shown_in or self.request.session_id,
                expected_version=suspension.execution.status_version,
            )
            if wait_kind == "system_access":
                data.update({
                    "wait_id": suspension.wait.wait_id,
                    "execution_id": self.attempt.execution_id,
                    "required_capabilities": list(wait_request.get("required_capabilities", [])),
                    "capabilities": list(wait_request.get("capabilities", [])),
                    "reason_code": "system_access_required",
                    "live": True,
                })
            sink({"type": frame_type, "data": data})
        except Exception:
            shared._log.exception("failed to publish durable approval wait")

    # -- Running: what each step adds to the journal ----------------------

    def _before_provider(self, payload) -> bool:
        snapshot = payload.get("resolved_snapshot")
        if not isinstance(snapshot, shared.Mapping):
            raise shared.AgentDriverError("checkpoint_schema_invalid", "provider action has no resolved snapshot")
        self.journal.record_snapshot(snapshot)
        # A request cut off mid-way is simply sent again; nothing downstream
        # needs the provider to recognize it.
        payload["supports_idempotency_key"] = False
        payload["idempotency_key"] = None
        return False

    def _before_tool(self, payload) -> bool:
        tool_call_id = str(payload.get("tool_call_id") or "")
        if not self.provider_action_id or not tool_call_id:
            raise shared.AgentDriverError("checkpoint_schema_invalid", "tool action has no provider decision")
        self.tool_arguments[tool_call_id] = dict(payload.get("arguments") or {})
        return False

    def _tool_started(self, payload) -> bool:
        tool_call_id = str(payload.get("tool_call_id") or "")
        call = next(
            (item for item in self.journal.state.last.tool_calls if str(item.get("id")) == tool_call_id), {},
        )
        self.journal.record_tool_started(
            tool_call_id, str(call.get("name") or ""), self.tool_arguments.get(tool_call_id, {}),
        )
        return False

    def _record_decision(self, payload) -> None:
        message = payload.get("message")
        if not isinstance(message, shared.Mapping):
            raise shared.AgentDriverError("checkpoint_schema_invalid", "provider receipt lacks AssistantMessage")
        self.journal.record_decision(
            message,
            action_id=_digest(self.attempt.execution_id, str(self.journal.position + 1)),
            turn=self._turn(payload),
            loaded_deferred_tools=payload.get("loaded_deferred_tools") or (),
        )

    def _record_tool_result(self, payload) -> None:
        result = payload.get("result")
        if not isinstance(result, shared.Mapping):
            raise shared.AgentDriverError("checkpoint_schema_invalid", "tool receipt lacks ToolResultMessage")
        is_error = bool(payload.get("is_error"))
        outcome = (
            "not_started" if payload.get("execution_started") is False
            else "failed" if is_error else "completed"
        )
        self.journal.record_tool_result(
            str(payload.get("tool_call_id") or ""), result, is_error=is_error, outcome=outcome,
            repeat_failures=payload.get("repeat_failures") or {},
            loaded_deferred_tools=payload.get("loaded_deferred_tools") or (),
        )

    # -- Commands that land between steps --------------------------------

    def _take_steers(self, service) -> None:
        """Queue open steering for the next model call."""
        if self.steer_queue is None:
            return
        taken = service.take_steers(
            execution_id=self.attempt.execution_id, attempt_id=self.attempt.attempt_id,
            generation=self.attempt.generation,
        )
        consumed = self.steer_consumed_ids or set()
        for command in taken:
            message = command.payload.get("message")
            if (command.command_id not in consumed
                    and not any(item.get("command_id") == command.command_id for item in self.steer_queue)
                    and isinstance(message, str) and message.strip()):
                self.steer_queue.append({
                    "command_id": command.command_id,
                    "payload": shared._json_safe(command.payload),
                })

    def _stop_for(self, service, command) -> bool:
        """Serve a pause or step command at the position just recorded."""
        if command.kind is CommandKind.STEER:
            self._take_steers(service)
            return False
        execution = service.executions.get_execution(self.attempt.execution_id)
        managed = None
        if command.kind is CommandKind.STEP:
            managed = {"action_id": _digest(self.provider_action_id, str(self.journal.position))}
        service.arrive_safe_point(
            attempt_id=self.attempt.attempt_id, generation=self.attempt.generation,
            command_id=command.command_id, expected_execution_version=execution.status_version,
            fragment=self._fragment(service, managed_action=managed),
        )
        return True

    def _after_step(self, service, kind: str, payload) -> bool:
        if kind == "provider.finished":
            self._record_decision(payload)
            return False
        if kind == "provider.after":
            self._record_decision(payload)
        elif kind == "tool.after":
            self._record_tool_result(payload)
        elif kind == "tool.suspended":
            self.journal.record_tool_suspended(str(payload.get("tool_call_id") or ""))
        else:
            raise shared.AgentDriverError("invalid_safe_point", "unsupported Agent safe point")
        command = self._current_command(service, self.attempt.execution_id)
        if command is not None:
            return self._stop_for(service, command)
        if kind == "tool.suspended":
            # The function's own wait resumes the turn from here.
            service.publish_agent_position(
                attempt_id=self.attempt.attempt_id, generation=self.attempt.generation,
                fragment=self._fragment(service),
            )
        return False

    # -- entry points ----------------------------------------------------

    def hook(self, kind: str, payload: shared.Mapping[str, shared.Any]) -> bool:
        """Route one Agent-loop boundary to the step that handles it."""
        service = self.driver._control_service()
        if kind == "provider.poll":
            return self._observe_provider_poll(service, payload)
        if kind == "tool.started":
            return self._tool_started(payload)
        if kind == "tool.before" and isinstance(payload.get("pre_wait"), shared.Mapping) and (
            str(payload["pre_wait"].get("kind") or "") == "system_access"
        ):
            self._recheck_system_access(payload)
        if kind == "tool.before" and isinstance(payload.get("pre_wait"), shared.Mapping):
            if self._wait_before_tool(service, payload):
                return True
        if kind == "provider.before":
            return self._before_provider(payload)
        if kind == "tool.before":
            return self._before_tool(payload)
        return self._after_step(service, kind, payload)

    def __call__(self, kind, payload):
        """Check for pause before any new dispatch, then run the boundary."""
        if (kind.endswith(".before") or kind == "tool.started") and self._pause_before_dispatch(
            self.driver._control_service(),
        ):
            return True
        return self.hook(kind, payload)


class SafePointsOperations:
    def _safe_point_hook(
        self,
        attempt: AttemptRecord,
        request: shared.Any,
        cancel_event: shared.threading.Event,
        *,
        continuation: AgentContinuation | None = None,
        steer_queue: list[dict[str, shared.Any]] | None = None,
        steer_consumed_ids: set[str] | None = None,
    ) -> shared.Callable[[str, shared.Mapping[str, shared.Any]], bool]:
        """Bind Agent-loop boundaries to the canonical execution owner.

        The closure carries only durable identifiers and JSON payloads.  It
        deliberately retains no provider stream, coroutine, tool object, or
        dispatcher-local state for a future attempt.
        """
        return _AgentSafePointHook(
            self, attempt, request, cancel_event,
            continuation, steer_queue, steer_consumed_ids,
        )
