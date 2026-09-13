"""AgentProductionDriver safe points operations."""
from __future__ import annotations
from . import shared


class SafePointsOperations:
    def _safe_point_hook(
        self,
        attempt: shared.AttemptRecord,
        request: shared.Any,
        cancel_event: shared.threading.Event,
        *,
        continuation: shared.AgentContinuation | None = None,
        steer_queue: list[dict[str, shared.Any]] | None = None,
        steer_consumed_ids: set[str] | None = None,
    ) -> shared.Callable[[str, shared.Mapping[str, shared.Any]], bool]:
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
            str, tuple[str, str, str, str | None, tuple[dict[str, shared.Any], ...]]
        ] = {}
        prior_actions: list[dict[str, shared.Any]] = []
        prior_receipts: list[dict[str, shared.Any]] = []
        completed_tool_results: list[dict[str, shared.Any]] = []
        decision_action_ids: set[str] = set()
        display_blocks: list[dict[str, shared.Any]] = []
        provider_action_id = ""
        provider_effect_id = ""
        provider_input_hash = ""
        provider_terminal_receipt: dict[str, shared.Any] | None = None
        latest_assistant: dict[str, shared.Any] | None = None
        latest_snapshot: dict[str, shared.Any] = {}
        if continuation is not None:
            for item in continuation.state.payload["completed_actions"]:
                action = dict(item)
                action["result"] = continuation.state.read_json_ref(
                    self.executions, continuation.checkpoint.execution_id, action["result_ref"],
                )
                prior_actions.append(action)
            for item in continuation.state.payload["terminal_effect_receipts"]:
                receipt = dict(item)
                receipt_ref = receipt.pop("receipt_ref", None)
                if not isinstance(receipt_ref, shared.Mapping):
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "terminal receipt ref is missing")
                try:
                    receipt["receipt"] = continuation.state.read_json_ref(
                        self.executions, continuation.checkpoint.execution_id, receipt_ref,
                    )
                except shared.AgentCheckpointError as exc:
                    raise shared.AgentDriverError(exc.code, str(exc)) from exc
                prior_receipts.append(receipt)
            completed_tool_results = [item.model_dump(mode="json") for item in continuation.tool_results]
            provider_action_id = continuation.provider_action_id
            latest_assistant = continuation.assistant_message.model_dump(mode="json")
            latest_snapshot = dict(continuation.resolved_snapshot)
            decision_action_ids.update(
                str(item.get("action_id"))
                for item in (*prior_actions, *prior_receipts)
                if isinstance(item.get("action_id"), str) and item["action_id"]
            )
            display_blocks.extend(
                shared.decode_turn_display(
                    continuation.state,
                    store=self.executions,
                    execution_id=continuation.checkpoint.execution_id,
                )
            )
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
                    if isinstance(candidate_receipt, shared.Mapping):
                        provider_terminal_receipt = dict(candidate_receipt)
                    break

        def digest(*parts: str) -> str:
            value = "\x1f".join(parts).encode("utf-8")
            return shared.hashlib.sha256(value).hexdigest()

        def json_digest(value: shared.Any) -> str:
            return shared.hashlib.sha256(shared.canonical_json_bytes(value)).hexdigest()

        def current_command(service, execution_id: str):
            commands = service.executions.list_commands(execution_id)
            priority = (CommandKind.PAUSE, CommandKind.STEP, CommandKind.STEER)
            for kind in priority:
                for command in commands:
                    if (
                        command.kind is kind
                        and command.status in {
                            shared.CommandStatus.ACCEPTED,
                            shared.CommandStatus.APPLYING,
                        }
                    ):
                        return command
            return None

        def checkpoint_payload(
            kind: str,
            payload: shared.Mapping[str, shared.Any],
            effect_id: str | None = None,
            action_id: str | None = None,
            input_hash: str | None = None,
            terminal_receipt: shared.Mapping[str, shared.Any] | None = None,
        ) -> shared.AgentCheckpointV1:
            phase = "after_provider" if kind in {"provider.after", "wait.before_tool", "tool.suspended"} else "after_tool"
            point_kind = (
                "agent.wait.before_tool" if kind == "wait.before_tool" else
                "agent.provider.decision.after" if phase == "after_provider"
                else "agent.tool.action.after"
            )
            execution = self._control_service().executions.get_execution(
                attempt.execution_id
            )
            if execution is None:
                raise shared.AgentDriverError("execution_not_found", "execution disappeared at safe point")
            raw_turn = payload.get("turn") if isinstance(payload.get("turn"), shared.Mapping) else {}
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
            if not isinstance(snapshot, shared.Mapping):
                snapshot = latest_snapshot
            if not isinstance(snapshot, shared.Mapping) or not snapshot:
                raise shared.AgentDriverError("checkpoint_schema_invalid", "Agent safe point has no resolved snapshot")
            if latest_assistant is None:
                raise shared.AgentDriverError("checkpoint_schema_invalid", "Agent safe point has no completed assistant message")
            tool_call_ids = [
                str(value) for value in payload.get("tool_call_ids", ())
            ]
            if not tool_call_ids:
                tool_call_ids = [
                    str(item.get("id"))
                    for item in latest_assistant.get("content", [])
                    if isinstance(item, shared.Mapping) and item.get("type") == "toolCall"
                ]
            next_tool_index = payload.get("next_tool_index")
            if not isinstance(next_tool_index, int):
                next_tool_index = 0 if phase == "after_provider" else len(completed_tool_results)
            # The Agent checkpoint is a resume cursor for the current provider
            # decision. Committed historical effects stay in the effect ledger;
            # copying them here overflows the bounded receipt/state-ref caps
            # and prevents pause after a long turn.
            if kind in {"provider.after", "provider.finished"}:
                keep_ids = {item for item in (action_id, provider_action_id) if item}
            else:
                keep_ids = set(decision_action_ids)
                if provider_action_id:
                    keep_ids.add(provider_action_id)
                if action_id:
                    keep_ids.add(action_id)
            action_values: list[dict[str, shared.Any]] = []
            seen_action_ids: set[str] = set()
            for item in prior_actions:
                item_id = item.get("action_id")
                if item_id in keep_ids and item_id not in seen_action_ids:
                    action_values.append(item)
                    seen_action_ids.add(item_id)
            receipt_values: list[dict[str, shared.Any]] = []
            seen_receipt_ids: set[str] = set()
            for item in prior_receipts:
                item_id = item.get("action_id")
                if item_id in keep_ids and item_id not in seen_receipt_ids:
                    receipt_values.append(item)
                    seen_receipt_ids.add(item_id)
            if kind == "wait.before_tool":
                if not provider_effect_id or not provider_input_hash or provider_terminal_receipt is None:
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "wait has no committed provider decision")
                if not any(item.get("action_id") == provider_action_id for item in action_values):
                    action_values.append({"action_id": provider_action_id, "input_hash": provider_input_hash, "result": latest_assistant})
                    seen_action_ids.add(provider_action_id)
                if not any(item.get("effect_id") == provider_effect_id for item in receipt_values):
                    receipt_values.append({
                        "effect_id": provider_effect_id,
                        "frontier_step_id": f"after_provider:{provider_action_id}",
                        "action_id": provider_action_id,
                        "outcome": "committed",
                        "receipt": dict(provider_terminal_receipt),
                    })
                    seen_receipt_ids.add(provider_action_id)
            if effect_id is not None:
                if action_id is None or input_hash is None or terminal_receipt is None:
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "effect safe point is missing a receipt")
                if action_id not in seen_action_ids:
                    action_values.append({"action_id": action_id, "input_hash": input_hash, "result": latest_assistant if phase == "after_provider" else completed_tool_results[-1]})
                if action_id not in seen_receipt_ids:
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
                if command.status not in {shared.CommandStatus.APPLIED, shared.CommandStatus.REJECTED}
                and command.command_id not in (steer_consumed_ids or ())
            ]
            try:
                return shared.AgentCheckpointV1.build(
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
                    loaded_deferred_tools=payload.get("loaded_deferred_tools", []),
                    turn_display=list(display_blocks),
                )
            except shared.AgentCheckpointError as exc:
                raise shared.AgentDriverError(exc.code, str(exc)) from exc

        def hook(kind: str, payload: shared.Mapping[str, shared.Any]) -> bool:
            nonlocal provider_action_id, provider_effect_id, provider_input_hash, provider_terminal_receipt, latest_assistant, latest_snapshot, completed_tool_results
            service = self._control_service()
            if kind == "tool.before" and isinstance(payload.get("pre_wait"), shared.Mapping):
                from openprogram.execution.checkpoints import CheckpointFragment
                from openprogram.execution.waits import DurableWaitStore, WaitStatus

                pre_wait = dict(payload["pre_wait"])
                tool_call_id = str(payload.get("tool_call_id") or "")
                if not provider_action_id or not tool_call_id:
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "wait has no stable provider or tool identity")
                wait_kind = str(pre_wait.get("kind") or "")
                if wait_kind not in {"approval", "ask", "ask_many", "confirm", "form", "system_access"}:
                    raise shared.AgentDriverError("invalid_wait", "wait kind is not supported at an Agent tool boundary")
                wait_id_parts = [
                    attempt.execution_id, provider_action_id, tool_call_id, wait_kind,
                ]
                if wait_kind == "system_access":
                    wait_id_parts.append(str(attempt.generation))
                wait_id = "wait_" + digest(*wait_id_parts)[:32]
                existing = DurableWaitStore(self.executions).get_wait(wait_id)
                if wait_kind == "system_access":
                    from openprogram.system_access import access_manifest_for_tool
                    # The manifest was captured while building the tool list;
                    # re-probe at the canonical boundary before opening or
                    # reusing a wait so a grant race does not suspend a task
                    # after access is already available.
                    refreshed = access_manifest_for_tool(
                        str(payload.get("tool_name") or ""),
                        dict(payload.get("arguments") or {}),
                    )
                    if refreshed is None:
                        return False
                    pre_wait = refreshed
                    if existing is not None and existing.status is WaitStatus.RESOLVED:
                        # A resolved receipt is tied to an earlier executor
                        # report. Never reuse it after a revocation race.
                        wait_id = "wait_" + digest(
                            attempt.execution_id, provider_action_id, tool_call_id,
                            wait_kind, str(attempt.generation), "recheck",
                        )[:32]
                        existing = DurableWaitStore(self.executions).get_wait(wait_id)
                if existing is not None:
                    if existing.status is WaitStatus.RESOLVED:
                        payload["preapproved_wait_id"] = wait_id
                    else:
                        # Decline/timeout/cancel policies settle the execution
                        # before a replacement attempt can reach this boundary.
                        # Do not dispatch the protected tool from an unresolved
                        # or non-approved record.
                        return True
                else:
                    checkpoint = checkpoint_payload("wait.before_tool", payload)
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
                        fragment=CheckpointFragment(
                            safe_point_kind="agent.wait.before_tool",
                            frontier=tuple(checkpoint.payload["frontier"]),
                            state_refs={},
                            completed_actions=tuple(), effect_receipts=tuple(),
                            pending_command_ids=tuple(checkpoint.payload["pending_command_ids"]),
                        ),
                        kind=wait_kind, request=wait_request,
                        policy_snapshot=dict(policy), expires_at=0 if timeout is None else shared.time.time() + float(timeout),
                        wait_id=wait_id, agent_checkpoint=checkpoint,
                    )
                    try:
                        sink = self.event_sink
                        if sink is None:
                            # Durable continuations can outlive the original
                            # chat transport. Their next wait still needs a
                            # live notification on the shared event stream.
                            from openprogram.events import emit_ws_frame
                            sink = emit_ws_frame
                        frame_type = "system_access.waiting" if wait_kind == "system_access" else "question.asked"
                        data = {
                            "id": suspension.wait.wait_id,
                            "session_id": request.session_id,
                            "kind": wait_kind, "prompt": wait_request["prompt"],
                            "options": wait_request["options"], "multi": wait_request["multi"],
                            "allow_custom": wait_request["allow_custom"], "detail": wait_request["detail"],
                            "schema": wait_request["schema"], "questions": wait_request["questions"],
                            "tool": wait_request.get("tool"), "args": wait_request.get("args"),
                            "risk_level": wait_request.get("risk_level"),
                            "allowed_scopes": suspension.wait.policy_snapshot.get("allowed_scopes"),
                            "execution_id": attempt.execution_id,
                            "wait_generation": suspension.wait.claim_generation,
                            "expected_version": suspension.execution.status_version,
                            "expires_at": suspension.wait.expires_at,
                        }
                        if wait_kind == "system_access":
                            data.update({
                                "wait_id": suspension.wait.wait_id,
                                "execution_id": attempt.execution_id,
                                "required_capabilities": list(wait_request.get("required_capabilities", [])),
                                "capabilities": list(wait_request.get("capabilities", [])),
                                "reason_code": "system_access_required",
                                "live": True,
                            })
                        sink({"type": frame_type, "data": data})
                    except Exception:
                        shared._log.exception("failed to publish durable approval wait")
                    return True
            if kind.endswith(".before"):
                execution = service.executions.get_execution(attempt.execution_id)
                if execution is None:
                    raise shared.AgentDriverError("execution_not_found", "execution disappeared before effect")
                if execution.status is shared.ExecutionStatus.CANCELLING or cancel_event.is_set():
                    from openprogram.providers.utils.errors import ExecInterrupt
                    raise ExecInterrupt("cancelled")
                if kind == "provider.before":
                    snapshot = payload.get("resolved_snapshot")
                    if not isinstance(snapshot, shared.Mapping):
                        raise shared.AgentDriverError("checkpoint_schema_invalid", "provider action has no resolved snapshot")
                    latest_snapshot = dict(snapshot)
                    context_hash = str(payload.get("normalized_context_hash") or json_digest(payload.get("context") or {}))
                    action_id = digest(
                        str(execution.revision_id),
                        str(attempt.generation),
                        str(execution.checkpoint_head_id or "root"),
                        context_hash,
                        json_digest(latest_snapshot),
                    )
                    input_hash = context_hash
                elif kind == "tool.before":
                    tool_call_id = str(payload.get("tool_call_id") or "")
                    if not provider_action_id or not tool_call_id:
                        raise shared.AgentDriverError("checkpoint_schema_invalid", "tool action has no provider decision")
                    action_id = digest(
                        str(execution.revision_id),
                        provider_action_id,
                        tool_call_id,
                        json_digest(payload.get("arguments") or {}),
                    )
                    input_hash = json_digest(payload.get("arguments") or {})
                else:
                    raise shared.AgentDriverError("invalid_safe_point", "unsupported Agent effect boundary")
                effect_id = f"effect_{action_id[:32]}"
                previous = service.effects.get(effect_id)
                if kind == "tool.before" and previous is not None and previous.receipt.get("function_suspended") is True:
                    action_id = digest(action_id, str(attempt.generation))
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
                    if isinstance(candidate, shared.Mapping)
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
                raise shared.AgentDriverError("effect_state_invalid", "Agent effect has no durable dispatch intent") from exc
            effect = service.effects.get(effect_id)
            if effect is None or effect.status is not EffectStatus.DISPATCHED:
                raise shared.AgentDriverError("effect_state_invalid", "Agent effect is not dispatchable")
            if kind in {"provider.after", "provider.finished"}:
                message = payload.get("message")
                if not isinstance(message, shared.Mapping):
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "provider receipt lacks AssistantMessage")
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
                    "stop_reason": message.get("stop_reason"),
                    "supports_idempotency_key": actual_supports_idempotency_key,
                }
                terminal_receipt["idempotency_key"] = (
                    idempotency_key if actual_supports_idempotency_key else None
                )
                provider_effect_id = effect_id
                provider_input_hash = input_hash
                provider_terminal_receipt = dict(terminal_receipt)
                for item in latest_assistant.get("content", []):
                    if not isinstance(item, shared.Mapping):
                        continue
                    item_type = item.get("type")
                    if item_type == "text" and isinstance(item.get("text"), str) and item["text"]:
                        display_blocks.append({"type": "text", "text": item["text"]})
                    elif item_type == "thinking" and isinstance(item.get("thinking"), str) and item["thinking"]:
                        display_blocks.append({"type": "thinking", "text": item["thinking"]})
                    elif item_type == "toolCall":
                        display_blocks.append({
                            "type": "tool",
                            "tool": item.get("name"),
                            "tool_call_id": item.get("id"),
                            "input": shared.json.dumps(item.get("arguments") or {}, default=str),
                        })
            elif kind == "tool.suspended":
                terminal_receipt = {"function_suspended": True, "tool_call_id": payload.get("tool_call_id")}
            elif kind == "tool.after":
                result = payload.get("result")
                if not isinstance(result, shared.Mapping):
                    raise shared.AgentDriverError("checkpoint_schema_invalid", "tool receipt lacks ToolResultMessage")
                completed_tool_results.append(dict(result))
                terminal_receipt = {
                    "tool_call_id": payload.get("tool_call_id"),
                    "is_error": bool(payload.get("is_error")),
                    "result_hash": json_digest(result),
                }
                tool_call_id = payload.get("tool_call_id")
                result_text = "\n".join(
                    str(item.get("text") or "")
                    for item in (result.get("content") or ())
                    if isinstance(item, shared.Mapping) and item.get("type") == "text"
                )
                for block in reversed(display_blocks):
                    if block.get("type") == "tool" and block.get("tool_call_id") == tool_call_id:
                        block["result"] = result_text
                        block["is_error"] = bool(payload.get("is_error"))
                        break
            else:
                raise shared.AgentDriverError("invalid_safe_point", "unsupported Agent safe point")
            def remember_completed_action() -> None:
                # Keep the current provider decision as the resume cursor.
                # Earlier committed writes are already in the effect ledger.
                prior_actions.append({
                    "action_id": action_id, "input_hash": input_hash,
                    "result": dict(latest_assistant) if kind in {"provider.after", "provider.finished"} else dict(result),
                })
                prior_receipts.append({
                    "effect_id": effect_id,
                    "frontier_step_id": f"{'after_provider' if kind in {'provider.after', 'provider.finished'} else 'after_tool'}:{action_id}",
                    "action_id": action_id, "outcome": "committed",
                    "receipt": dict(terminal_receipt),
                })
                if kind in {"provider.after", "provider.finished"}:
                    decision_action_ids.clear()
                    decision_action_ids.add(action_id)
                    prior_actions[:] = [
                        item for item in prior_actions if item.get("action_id") == action_id
                    ]
                    prior_receipts[:] = [
                        item for item in prior_receipts if item.get("action_id") == action_id
                    ]
                else:
                    decision_action_ids.add(action_id)

            command = None if kind == "provider.finished" else current_command(service, attempt.execution_id)
            from openprogram.execution.restart import window_seconds
            # Direct non-durable hook callers do not carry a revision contract.
            restart_window = window_seconds() if getattr(request, "_execution_revision_id", None) or continuation is not None else 0
            if command is None and (kind == "provider.finished" or restart_window == 0):
                service.effects.resolve(
                    effect_id, expected_status=EffectStatus.DISPATCHED,
                    outcome=EffectStatus.COMMITTED, receipt=terminal_receipt,
                    attempt_id=attempt.attempt_id, generation=attempt.generation,
                )
                remember_completed_action()
                return False
            current = service.executions.get_execution(attempt.execution_id)
            if current is None:
                raise shared.AgentDriverError("execution_not_found", "execution disappeared at safe point")
            checkpoint = checkpoint_payload(
                kind, payload, effect_id, action_id, input_hash, terminal_receipt,
            )
            completion = service.commit_agent_safe_point(
                execution_id=attempt.execution_id, attempt_id=attempt.attempt_id,
                generation=attempt.generation, expected_version=current.status_version,
                safe_point_kind=str(checkpoint.payload["safe_point"]["kind"]),
                frontier=tuple(checkpoint.payload["frontier"]),
                state_refs={"restart_window_seconds": restart_window},
                effect_id=effect_id, terminal_receipt=terminal_receipt,
                receipt_blob=shared.canonical_json_bytes(terminal_receipt),
                agent_checkpoint=checkpoint,
                command_id=command.command_id if command is not None else None, managed_action_id=action_id,
                consumed_steer_command_ids=tuple(sorted(steer_consumed_ids or ())),
            )
            if kind != "tool.suspended":
                remember_completed_action()
            if command is not None and command.kind is CommandKind.STEER and steer_queue is not None:
                consumed = steer_consumed_ids or set()
                for applied in completion.applied_commands:
                    if applied.kind is not CommandKind.STEER:
                        continue
                    if applied.command_id in consumed or any(
                        item.get("command_id") == applied.command_id for item in steer_queue
                    ):
                        continue
                    message = applied.payload.get("message")
                    if isinstance(message, str) and message.strip():
                        steer_queue.append({
                            "command_id": applied.command_id,
                            "payload": {"message": message},
                        })
            return command is not None and command.kind in {CommandKind.PAUSE, CommandKind.STEP}

        return hook

