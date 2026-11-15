"""The agent run loop: model turns, tool rounds, steering and recovery until the turn ends."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventRetry,
    EventStructuredOutputEnd,
    EventStructuredOutputRetry,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.models.providers.utils.overflow import is_context_overflow
from openprogram.runtime.agent.loop.types import (
    AgentContext,
    AgentEvent,
    AgentEventAgentEnd,
    AgentEventMessageEnd,
    AgentEventMessageStart,
    AgentEventMessageUpdate,
    AgentEventTurnEnd,
    AgentEventTurnStart,
    AgentLoopConfig,
    AgentMessage,
    StreamFn,
)

from openprogram.runtime.agent.loop.guards import (
    RepetitiveOutputError,
    _SafePointStop,
    _SteeredResponse,
    iteration_cap_for,
)
from openprogram.runtime.agent.loop.message_context import _durable_message
from openprogram.runtime.agent.loop.streaming import _stream_assistant_response
from openprogram.runtime.agent.loop.tool_dispatch import _execute_tool_calls
from openprogram.runtime.agent.loop.progress_note import QuietStretch
from openprogram.runtime.agent.loop.tool_loop_guard import ToolLoopGuard


#: Compactions one turn may run to recover from a context overflow.
OVERFLOW_COMPACTIONS_PER_TURN = 2


async def _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn):
    from openprogram.models.providers.utils.recovery import RecoveryState, current_recovery

    token = None
    if current_recovery.get() is None:
        limit = config.response_format.max_validation_retries if config.response_format else 2
        token = current_recovery.set(RecoveryState(limit=limit))
    from openprogram.models.providers.structured_output import StructuredOutputError
    from openprogram.models.providers.utils.errors import ExecInterrupt

    from openprogram.programs._authoring import guides
    guides_token = guides.begin_loop()
    state = current_recovery.get()
    try:
        await _run_loop_with_recovery(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
    except (ExecInterrupt, asyncio.CancelledError):
        state.mark_cancelled()
        raise
    except StructuredOutputError:
        state.mark_paused()
        raise
    finally:
        guides.end_loop(guides_token)
        if token is not None:
            current_recovery.reset(token)


async def _run_loop_with_recovery(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    stream_fn: StreamFn | None,
) -> None:
    """
    Main loop logic — mirrors runLoop() in TypeScript.
    """
    # Keep this import at function scope.  Repetitive-output recovery can
    # raise the same cancellation boundary as structured recovery; importing
    # it only in a later branch makes that path an UnboundLocalError.
    from openprogram.models.providers.utils.errors import ExecInterrupt

    first_turn = True
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages()

    from openprogram.models.providers.catalog.api_registry import resolve_api_provider_snapshot

    provider_snapshot = resolve_api_provider_snapshot(config.model)
    structured_plan = None
    if config.response_format is not None:
        from openprogram.models.providers.structured_output import negotiate_structured_output

        structured_plan = negotiate_structured_output(
            config.model,
            provider_snapshot.structured_output,
            config.response_format,
            list(current_context.tools or []),
            tool_choice=config.tool_choice,
            parallel_tool_calls=config.parallel_tool_calls,
        )
    if config.stop_after_tool_round and config.response_format is not None:
        raise ValueError("stop_after_tool_round cannot be combined with response_format")
    structured_attempt = 1
    pending_validation_error: Exception | None = None

    def commit_assistant(message: AssistantMessage) -> None:
        if structured_plan is not None:
            current_context.messages.append(message)
            ev_stream.push(AgentEventMessageEnd(message=message))
        new_messages.append(message)

    async def finish_provider_response(message: AssistantMessage) -> None:
        # A returned response is known even when it cannot become a decision.
        if config.safe_point_hook is not None:
            await config.safe_point_hook("provider.finished", {
                "message": _durable_message(message),
                "usage": _durable_message(message.usage),
            })

    async def schedule_structured_repair(
        error: Exception,
        candidate: AssistantMessage,
    ) -> bool:
        nonlocal structured_attempt, has_more_tool_calls, pending_validation_error
        if candidate.stop_reason != "length":
            await finish_provider_response(candidate)
        if structured_plan is None or config.response_format is None:
            return False
        from openprogram.models.providers.structured_output import (
            StructuredOutputValidationError,
            StructuredOutputGenerationError,
            build_repair_prompt,
        )

        incomplete = isinstance(error, StructuredOutputGenerationError) and error.code == "incomplete"
        if not isinstance(error, StructuredOutputValidationError) and not incomplete:
            return False
        if cancel_event is not None and cancel_event.is_set():
            from openprogram.models.providers.utils.errors import ExecInterrupt
            from openprogram.models.providers.utils.recovery import current_recovery
            state = current_recovery.get()
            if state is not None:
                state.mark_cancelled()
            raise ExecInterrupt("cancelled")
        if structured_attempt > config.response_format.max_validation_retries:
            return False
        if iteration_cap is not None and inner_iterations >= iteration_cap:
            return False
        from openprogram.models.providers.utils.recovery import reserve_recovery
        if not reserve_recovery(error.code):
            return False
        next_attempt = structured_attempt + 1
        ev_stream.push(AgentEventMessageUpdate(
            message=candidate,
            assistant_message_event=EventStructuredOutputRetry(
                attempt=structured_attempt,
                next_attempt=next_attempt,
                issues=error.issues,
            ),
        ))
        # A truncated tool call is not a valid assistant protocol turn.
        # Keep completed tools in context, but never include partial arguments.
        if not incomplete:
            current_context.messages.append(candidate)
        current_context.messages.append(UserMessage(
            content=(
                "The previous generation was incomplete. Generate the complete required "
                "JSON value from the existing evidence. Do not continue a partial JSON "
                "prefix or repeat completed tool operations. Avoid redundant explanation; "
                "preserve all required facts and fields."
                if incomplete else build_repair_prompt(error)
            ),
            timestamp=int(time.time() * 1000),
        ))
        structured_attempt = next_attempt
        pending_validation_error = error
        has_more_tool_calls = True
        return True

    # ``runtime.exec`` passes max_iterations=20. Chat turns pass None
    # and run until text, cancel, or error — same as Codex / DeepSeek.
    iteration_cap = iteration_cap_for(config.max_iterations)
    inner_iterations = 0

    # Overflow recovery (context/compaction.html, "Reactive"): the request
    # that overflowed is sent again once after a compaction, never the turn.
    # One compaction per request, two per turn, so a compaction that does not
    # help cannot loop (Codex's "retry once per model step").
    overflow_compactions = 0
    retrying_after_overflow = False
    mid_turn_compacted = False

    async def recover_from_overflow() -> bool:
        """Compact and replace the loop's messages; whether to send again."""
        nonlocal overflow_compactions, retrying_after_overflow
        if (config.compact_turn is None or structured_plan is not None
                or retrying_after_overflow
                or overflow_compactions >= OVERFLOW_COMPACTIONS_PER_TURN):
            return False
        overflow_compactions += 1
        replaced = await config.compact_turn(list(current_context.messages), "reactive")
        if cancel_event is not None and cancel_event.is_set():
            raise ExecInterrupt("cancelled")
        if replaced is None:
            return False
        current_context.messages = replaced
        retrying_after_overflow = True
        return True

    while True:
        has_more_tool_calls = True
        steering_after_tools: list[AgentMessage] | None = None
        repeat_failures: dict[str, int] = {}
        tool_loop = ToolLoopGuard()
        quiet = QuietStretch()
        incomplete_responses = 0

        while has_more_tool_calls or len(pending_messages) > 0:
            inner_iterations += 1
            if iteration_cap is not None and inner_iterations > iteration_cap:
                if pending_validation_error is not None:
                    raise pending_validation_error
                if structured_plan is not None and structured_plan.mode == "tool":
                    from openprogram.models.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    raise StructuredOutputValidationError(
                        "The model did not call the hidden structured-output submission tool",
                        code="missing_submission",
                    )
                error = RuntimeError(
                    f"Agent iteration limit ({iteration_cap}) reached with pending work"
                )
                error.retryable = False
                raise error
            if not first_turn:
                ev_stream.push(AgentEventTurnStart())
            else:
                first_turn = False

            # Inject pending messages
            if pending_messages:
                for msg in pending_messages:
                    ev_stream.push(AgentEventMessageStart(message=msg))
                    ev_stream.push(AgentEventMessageEnd(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # Stream assistant response
            steered_response = False
            from openprogram.models.providers.structured_output import StructuredOutputGenerationError
            try:
                message = await _stream_assistant_response(
                    current_context,
                    config,
                    cancel_event,
                    ev_stream,
                    stream_fn,
                    structured_plan,
                    provider_snapshot,
                    structured_attempt if structured_plan is not None else None,
                )
                if isinstance(message, _SteeredResponse):
                    steered_response = True
                    message = message.message
            except _SafePointStop:
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                return
            except RepetitiveOutputError as error:
                from openprogram.models.providers.utils.recovery import (
                    current_recovery,
                    reserve_recovery,
                )

                state = current_recovery.get()
                has_recovery_budget = bool(
                    state is None
                    or (state.phase != "cancelled" and state.used < state.limit)
                )
                failed_message = AssistantMessage(
                    content=[],
                    api=config.model.api,
                    provider=config.model.provider,
                    model=config.model.id,
                    stop_reason="error",
                    error_message=error.code,
                    error_retryable=(
                        has_recovery_budget
                        and not (cancel_event is not None and cancel_event.is_set())
                    ),
                    error_transport_exhausted=(
                        has_recovery_budget is False
                    ),
                    timestamp=int(time.time() * 1000),
                )
                # The provider.before durable effect must be closed even
                # though this response has no terminal provider event.  This
                # mirrors the incomplete structured-output path and prevents
                # the next durable request from seeing an unfinished effect.
                await finish_provider_response(failed_message)
                if cancel_event is not None and cancel_event.is_set():
                    if state is not None:
                        state.mark_cancelled()
                    raise ExecInterrupt("cancelled")
                if not reserve_recovery(error.code):
                    if state is not None:
                        state.mark_paused()
                    error.transport_exhausted = True
                    error.retryable = False
                    raise
                # The runtime stream projection already treats this event as
                # an attempt boundary.  Keep the actual reason in ``issues``
                # so the existing projection starts a fresh visible attempt
                # without presenting this as a schema-validation failure.
                attempt = max(1, int(state.requests)) if state is not None else 1
                ev_stream.push(AgentEventMessageUpdate(
                    message=failed_message,
                    assistant_message_event=EventStructuredOutputRetry(
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        issues=[{
                            "code": error.code,
                            "message": "provider response repeated text without progress",
                        }],
                    ),
                ))
                # Discard the failed provider response, but keep every
                # completed tool result already installed in this context.
                # The next model call stays inside this agent loop, so the
                # tool executor cannot replay an external operation.
                current_context.messages.append(UserMessage(
                    content=(
                        "The previous response repeated the same text without "
                        "making progress. Continue from the completed tool "
                        "results, use an available tool when needed, and "
                        "produce one complete response without repeating that text."
                    ),
                    timestamp=int(time.time() * 1000),
                ))
                continue
            except StructuredOutputGenerationError as error:
                if error.code != "incomplete" or structured_plan is None:
                    raise
                # EOF is a failed response, not a valid partial assistant turn.
                # Retain earlier completed tool rounds and discard this candidate.
                candidate = AssistantMessage(
                    content=[], api=config.model.api, provider=config.model.provider,
                    model=config.model.id, stop_reason="error", error_message=str(error),
                    timestamp=int(time.time() * 1000),
                )
                if await schedule_structured_repair(error, candidate):
                    continue
                raise
            except Exception as error:
                from openprogram.runtime.context.compaction.reactive import is_overflow_error
                if not is_overflow_error(error):
                    raise
                failed = AssistantMessage(
                    content=[], api=config.model.api, provider=config.model.provider,
                    model=config.model.id, stop_reason="error", error_message=str(error),
                    timestamp=int(time.time() * 1000),
                )
                last = current_context.messages[-1] if current_context.messages else None
                if isinstance(last, AssistantMessage) and not any(last is m for m in new_messages):
                    # What the failed response streamed before it failed.
                    current_context.messages.pop()
                if not await recover_from_overflow():
                    raise
                await finish_provider_response(failed)
                has_more_tool_calls = True
                continue

            if (not steered_response and message.stop_reason == "error"
                    and is_context_overflow(message)):
                # The overflowed response is not part of the turn: it leaves
                # the messages before the compaction rebuilds them.
                if current_context.messages and current_context.messages[-1] is message:
                    current_context.messages.pop()
                if await recover_from_overflow():
                    await finish_provider_response(message)
                    has_more_tool_calls = True
                    continue
                current_context.messages.append(message)
            elif not steered_response and message.stop_reason != "error":
                retrying_after_overflow = False

            if not steered_response and (message.stop_reason in ("error", "aborted") or (
                structured_plan is not None and message.stop_reason == "length"
            )):
                await finish_provider_response(message)

            if structured_plan is not None and message.stop_reason in (
                "length", "error", "aborted",
            ):
                from openprogram.models.providers.structured_output import (
                    StructuredOutputGenerationError,
                )

                if message.stop_reason == "aborted":
                    from openprogram.models.providers.utils.errors import ExecInterrupt
                    raise ExecInterrupt("aborted")
                if message.stop_reason == "error" and message.error_message:
                    reason = message.error_message.lower()
                    if not any(
                        marker in reason
                        for marker in ("refusal", "content filter", "content_filter", "safety")
                    ):
                        commit_assistant(message)
                        ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                        ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                        ev_stream.end(new_messages)
                        return
                code = "incomplete" if message.stop_reason == "length" else "refusal"
                error = StructuredOutputGenerationError(
                    "Structured output generation did not produce a complete value",
                    code=code,
                    issues=[{"code": code, "message": "Provider stopped before a complete value", "path": ""}],
                )
                if code == "incomplete" and await schedule_structured_repair(error, message):
                    continue
                raise error

            if not steered_response and message.stop_reason in ("error", "aborted"):
                commit_assistant(message)
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            # Check for tool calls
            tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
            has_more_tool_calls = len(tool_calls) > 0

            # A provider can exhaust its output budget entirely on thinking,
            # or stop at its token limit before answering. Neither is a
            # completed agent turn. Keep completed tool effects in context and
            # ask for a continuation. Only consecutive turns without visible
            # progress count toward the no-progress limit.
            visible_text = "".join(
                block.text for block in message.content
                if isinstance(block, TextContent)
            ).strip()
            if structured_plan is None and not steered_response and not has_more_tool_calls and (
                message.stop_reason == "length" or not visible_text
            ):
                await finish_provider_response(message)
                incomplete_responses = 0 if visible_text else incomplete_responses + 1
                if incomplete_responses > 2:
                    raise RuntimeError(
                        "model_response_incomplete: provider stopped without a complete answer"
                    )
                ev_stream.push(AgentEventMessageUpdate(
                    message=message,
                    assistant_message_event=EventRetry(
                        attempt=2 if visible_text else incomplete_responses + 1,
                        max_attempts=None if visible_text else 3,
                        reason="output_limit" if message.stop_reason == "length" and visible_text
                        else "incomplete_response",
                    ),
                ))
                commit_assistant(message)
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                current_context.messages.append(UserMessage(
                    content=(
                        "Continue from the completed work and give a complete "
                        "answer to the user's request. Do not repeat tool calls "
                        "that have already succeeded."
                    ),
                    timestamp=int(time.time() * 1000),
                ))
                has_more_tool_calls = True
                continue
            if visible_text or has_more_tool_calls:
                incomplete_responses = 0

            if structured_plan is not None and structured_plan.mode == "tool":
                submit_calls = [
                    call
                    for call in tool_calls
                    if call.name == structured_plan.submit_tool_name
                ]
                if submit_calls and len(tool_calls) != 1:
                    from openprogram.models.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    error = StructuredOutputValidationError(
                        "The hidden structured-output submission must be the only tool call",
                        code="mixed_submission",
                    )
                    if await schedule_structured_repair(error, message):
                        continue
                    raise error
                if submit_calls:
                    from openprogram.models.providers.structured_output import parse_and_validate_json

                    validation_output = replace(
                        config.response_format,
                        schema=structured_plan.original_schema,
                    )
                    try:
                        value = parse_and_validate_json(
                            json.dumps(submit_calls[0].arguments, ensure_ascii=False),
                            validation_output,
                        )
                    except Exception as error:
                        if await schedule_structured_repair(error, message):
                            continue
                        raise
                    message.content = [TextContent(
                        text=json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )]
                    message.stop_reason = "stop"
                    message.structured_output = value
                    message.structured_output_mode = "tool"
                    message.structured_output_attempt = structured_attempt
                    has_more_tool_calls = False
                elif not tool_calls:
                    from openprogram.models.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    error = StructuredOutputValidationError(
                        "The model did not call the hidden structured-output submission tool",
                        code="missing_submission",
                    )
                    if await schedule_structured_repair(error, message):
                        continue
                    raise error

            if (
                structured_plan is not None
                and structured_plan.mode in ("native", "prompt")
                and not tool_calls
            ):
                from openprogram.models.providers.structured_output import parse_and_validate_json

                raw = "".join(
                    block.text for block in message.content
                    if isinstance(block, TextContent)
                )
                validation_output = replace(
                    config.response_format,
                    schema=structured_plan.original_schema,
                )
                try:
                    value = parse_and_validate_json(raw, validation_output)
                except Exception as error:
                    if await schedule_structured_repair(error, message):
                        continue
                    raise
                message.content = [TextContent(
                    text=json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )]
                message.structured_output = value
                message.structured_output_mode = structured_plan.mode
                message.structured_output_attempt = structured_attempt

            if structured_plan is not None and message.structured_output_mode is not None:
                pending_validation_error = None
                ev_stream.push(AgentEventMessageUpdate(
                    message=message,
                    assistant_message_event=EventStructuredOutputEnd(
                        attempt=structured_attempt,
                        mode=message.structured_output_mode,
                        value=message.structured_output,
                    ),
                ))
                ev_stream.push(AgentEventMessageUpdate(
                    message=message,
                    assistant_message_event=EventDone(reason="stop", message=message),
                ))

            commit_assistant(message)

            stop_at_safe_point = False
            if config.safe_point_hook is not None:
                stop_at_safe_point = bool(await config.safe_point_hook(
                    "provider.after",
                    {
                        "message": _durable_message(message),
                        "tool_call_ids": [
                            str(call.id) for call in message.content
                            if isinstance(call, ToolCall)
                        ],
                        "next_tool_index": 0,
                        "interrupted_by_steer": steered_response,
                        "usage": _durable_message(message.usage),
                    },
                ))

            if stop_at_safe_point:
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            tool_results: list[ToolResultMessage] = []
            if has_more_tool_calls:
                execution = await _execute_tool_calls(
                    current_context.tools,
                    message,
                    cancel_event,
                    ev_stream,
                    config.get_steering_messages,
                    repeat_failures,
                    config.safe_point_hook,
                    round_id=f"turn:{inner_iterations}",
                    context_window=getattr(config.model, "context_window", None),
                    tool_output_weight=getattr(config.model, "tool_output_weight", None),
                    history=current_context.messages,
                )
                tool_results.extend(execution["tool_results"])
                steering_after_tools = execution.get("steering_messages")

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

                if execution.get("stop_at_safe_point"):
                    ev_stream.push(AgentEventTurnEnd(message=message, tool_results=tool_results))
                    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                    ev_stream.end(new_messages)
                    return

            ev_stream.push(AgentEventTurnEnd(message=message, tool_results=tool_results))

            if config.stop_after_tool_round and tool_results:
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            if steering_after_tools:
                pending_messages = steering_after_tools
                steering_after_tools = None
            else:
                pending_messages = []
                if config.get_steering_messages:
                    pending_messages = await config.get_steering_messages()

            if (config.compact_turn is not None and tool_results and not mid_turn_compacted
                    and structured_plan is None):
                # A step boundary: every result of this step is in. When the
                # next request would pass the automatic threshold, the turn's
                # history is compacted for good now, once per turn, instead of
                # being summarised again on every request (Codex's MidTurn).
                replaced = await config.compact_turn(list(current_context.messages), "mid_turn")
                if replaced is not None:
                    current_context.messages = replaced
                    mid_turn_compacted = True

            if pending_messages:
                quiet.reset()
            elif config.progress_notes and tool_results and (note := quiet.observe(message)):
                # For this turn only: it goes to the model after the results and is not kept.
                current_context.messages.append(UserMessage(content=note, timestamp=int(time.time() * 1000)))

            # Anything the user says mid-turn is progress; otherwise a round
            # that repeats the last ones exactly ends the turn with a notice.
            if pending_messages:
                tool_loop.reset()
            elif tool_results and (stuck := tool_loop.observe(message, tool_results)):
                notice = AssistantMessage(
                    content=[],
                    api=config.model.api,
                    provider=config.model.provider,
                    model=config.model.id,
                    stop_reason="error",
                    error_message=stuck.notice(),
                    error_retryable=False,
                    timestamp=int(time.time() * 1000),
                )
                ev_stream.push(AgentEventMessageStart(message=notice))
                ev_stream.push(AgentEventMessageEnd(message=notice))
                new_messages.append(notice)
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

        # Check for follow-up messages
        follow_up_messages: list[AgentMessage] = []
        if config.get_follow_up_messages:
            follow_up_messages = await config.get_follow_up_messages()

        if follow_up_messages:
            pending_messages = follow_up_messages
            continue

        break

    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
    ev_stream.end(new_messages)
