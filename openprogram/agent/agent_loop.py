"""
Agent loop — mirrors packages/agent/src/agent-loop.ts

Core loop logic: agentLoop(), agentLoopContinue(), runLoop().
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import replace
from typing import Any, AsyncGenerator

from openprogram.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStructuredOutputEnd,
    EventStructuredOutputRetry,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.validation import validate_tool_arguments

from openprogram.events import emit_safe, get_event_bus, make_event
from openprogram.events import ToolGateDenied, decide_tool_gate
from .types import (
    AgentContext,
    AgentEvent,
    AgentEventAgentEnd,
    AgentEventAgentStart,
    AgentEventMessageEnd,
    AgentEventMessageStart,
    AgentEventMessageUpdate,
    AgentEventToolEnd,
    AgentEventToolStart,
    AgentEventToolUpdate,
    AgentEventTurnEnd,
    AgentEventTurnStart,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    StreamFn,
)


def _latest_user_text(messages: list) -> str:
    """Walk back from the end and return the last user-role text.

    Memory prefetch uses this as the recall query for the upcoming
    turn. Empty string if no user message is present (e.g. on the
    first model warmup call).
    """
    for msg in reversed(messages):
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict):
                    if c.get("type") == "text" or "text" in c:
                        parts.append(str(c.get("text", "")))
                else:
                    text = getattr(c, "text", None)
                    if text:
                        parts.append(str(text))
            joined = " ".join(p for p in parts if p)
            if joined.strip():
                return joined.strip()
        return ""
    return ""


def _inject_memory_prefetch(llm_messages: list, block: str) -> bool:
    """Prepend ``block`` to the last user message's text, in place.

    dag/overview.md §7 — prefetched memory belongs to the turn that recalled
    it, not to the session-constant system prompt. Tool results carry role
    ``toolResult``, so the last ``role == "user"`` message is always the
    conversational turn. Returns True when a message was modified.
    """
    prefix = block.rstrip() + "\n\n"
    for msg in reversed(llm_messages or []):
        role = getattr(msg, "role", None) or (
            msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            _set_content(msg, prefix + content)
            return True
        if isinstance(content, list):
            for part in content:
                ptype = getattr(part, "type", None) or (
                    part.get("type") if isinstance(part, dict) else None)
                if ptype != "text":
                    continue
                if isinstance(part, dict):
                    part["text"] = prefix + str(part.get("text") or "")
                else:
                    part.text = prefix + (getattr(part, "text", "") or "")
                return True
            # Image/file-only turn: no text part to prefix — leave it alone
            # rather than inventing a block ordering the provider may reject.
        return False
    return False


def _set_content(msg, value) -> None:
    if isinstance(msg, dict):
        msg["content"] = value
    else:
        msg.content = value


def _durable_message(message: Any) -> dict[str, Any]:
    """Return JSON data only; checkpoints never retain provider objects."""
    if hasattr(message, "model_dump"):
        try:
            value = message.model_dump(mode="json")
        except Exception:
            # AgentTool contains its executable callback, which is not a
            # durable value.  A checkpoint needs the resolved public schema,
            # not a repr of that callback or a process-local object address.
            if all(hasattr(message, field) for field in ("name", "description", "parameters")):
                value = {
                    "name": message.name,
                    "description": message.description,
                    "parameters": message.parameters,
                }
            else:
                value = {"repr": str(message)}
    elif isinstance(message, dict):
        value = dict(message)
    else:
        value = {"repr": str(message)}
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _create_agent_stream() -> EventStream[AgentEvent, list[AgentMessage]]:
    return EventStream(
        is_done=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages if e.type == "agent_end" else [],
    )


def _record_job_activity(kind: str) -> None:
    try:
        from openprogram.agent.job.runner import record_current_job_activity
        record_current_job_activity(kind)
    except Exception:
        pass


def _job_operation_timeout(declared: float | None) -> float | None:
    from openprogram.agent.job.runner import current_job_operation_timeout
    return current_job_operation_timeout(declared)


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent, list[AgentMessage]]:
    """
    Start an agent loop with new prompt messages.
    Mirrors agentLoop() in TypeScript.
    """
    ev_stream = _create_agent_stream()

    async def _run():
        try:
            new_messages: list[AgentMessage] = list(prompts)
            current_context = AgentContext(
                system_prompt=context.system_prompt,
                messages=list(context.messages) + list(prompts),
                tools=context.tools,
                memory_prefetch=context.memory_prefetch,
                runtime_contract=context.runtime_contract,
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())
            for prompt in prompts:
                ev_stream.push(AgentEventMessageStart(message=prompt))
                ev_stream.push(AgentEventMessageEnd(message=prompt))

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            # Ensure the stream is always terminated even if the loop crashes
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)
        except BaseException as e:
            # User-triggered CancelledError (BaseException subclass) — end the
            # stream cleanly so the chat dispatcher unblocks and the running_task
            # gets cleared. Without this branch the Task dies with an unretrieved
            # exception and the UI is stuck on the stop button.
            from openprogram.agentic_programming.function import (
                CancelledError as _AgenticCancelled,
            )
            if isinstance(e, _AgenticCancelled):
                if not ev_stream._result_event.is_set():
                    ev_stream.end(new_messages)
            else:
                from openprogram.providers.utils.errors import ExecInterrupt
                if isinstance(e, ExecInterrupt):
                    if not ev_stream._result_event.is_set():
                        ev_stream.fail(e)
                else:
                    raise

    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent, list[AgentMessage]]:
    """
    Continue from the current context without adding a new message.
    Mirrors agentLoopContinue() in TypeScript.
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    last = context.messages[-1]
    if hasattr(last, "role") and last.role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    ev_stream = _create_agent_stream()

    async def _run():
        try:
            new_messages: list[AgentMessage] = []
            current_context = AgentContext(
                system_prompt=context.system_prompt,
                messages=list(context.messages),
                tools=context.tools,
                memory_prefetch=context.memory_prefetch,
                runtime_contract=context.runtime_contract,
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)
        except BaseException as e:
            from openprogram.providers.utils.errors import ExecInterrupt
            if isinstance(e, ExecInterrupt):
                if not ev_stream._result_event.is_set():
                    ev_stream.fail(e)
            else:
                raise

    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


def agent_loop_resume(
    continuation: Any,
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent, list[AgentMessage]]:
    """Resume one durable Agent frontier without a provider replay.

    The provider response stored at ``after_provider`` is a completed action.
    This producer restores that decision and executes only its remaining tools;
    ``after_tool`` restores the completed result sequence then asks the next
    provider decision.  It never receives a live stack or stream from the old
    attempt.
    """
    ev_stream = _create_agent_stream()

    async def _run() -> None:
        new_messages: list[AgentMessage] = []
        try:
            current_context = AgentContext(
                system_prompt=context.system_prompt,
                messages=list(context.messages),
                tools=context.tools,
                memory_prefetch=context.memory_prefetch,
                runtime_contract=context.runtime_contract,
            )
            assistant = continuation.assistant_message
            current_context.messages.append(assistant)
            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            tool_calls = [
                call for call in assistant.content if isinstance(call, ToolCall)
            ]
            if continuation.phase == "after_tool":
                current_context.messages.extend(continuation.tool_results)
            elif continuation.phase != "after_provider":
                raise ValueError("unsupported Agent continuation phase")

            # A provider decision may contain several calls.  An after-tool
            # checkpoint has committed only the prefix through
            # ``next_tool_index``; resume that exact suffix instead of asking
            # the provider again or replaying a completed tool.
            start_index = continuation.next_tool_index
            if start_index < len(tool_calls):
                execution = await _execute_tool_calls(
                    current_context.tools,
                    assistant,
                    cancel_event,
                    ev_stream,
                    config.get_steering_messages,
                    continuation.repeat_failures,
                    config.safe_point_hook,
                    start_index=start_index,
                )
                tool_results = execution["tool_results"]
                current_context.messages.extend(tool_results)
                new_messages.extend(tool_results)
                if execution.get("stop_at_safe_point"):
                    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                    ev_stream.end(new_messages)
                    return

            # A completed provider answer with no tool calls is itself the
            # terminal assistant result.  It must be persisted/finalized by
            # the continuation dispatcher, not sent through a second provider
            # request.
            if continuation.phase == "after_provider" and not tool_calls:
                new_messages.append(assistant)
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            # The restored provider decision is already durable.  Continue
            # with the next decision only after all stored/remaining tool
            # results have been installed in the rebuilt context.
            await _run_loop(
                current_context, new_messages, config, cancel_event, ev_stream,
                stream_fn,
            )
        except Exception as exc:
            if not ev_stream._result_event.is_set():
                ev_stream.fail(exc)
        except BaseException as exc:
            from openprogram.providers.utils.errors import ExecInterrupt
            if isinstance(exc, ExecInterrupt):
                if not ev_stream._result_event.is_set():
                    ev_stream.fail(exc)
            else:
                raise

    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


async def _run_loop(
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
    first_turn = True
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages()

    from openprogram.providers.api_registry import resolve_api_provider_snapshot

    provider_snapshot = resolve_api_provider_snapshot(config.model)
    structured_plan = None
    if config.response_format is not None:
        from openprogram.providers.structured_output import negotiate_structured_output

        structured_plan = negotiate_structured_output(
            config.model,
            provider_snapshot.structured_output,
            config.response_format,
            list(current_context.tools or []),
            tool_choice=config.tool_choice,
            parallel_tool_calls=config.parallel_tool_calls,
        )
    structured_attempt = 1
    pending_validation_error: Exception | None = None

    def commit_assistant(message: AssistantMessage) -> None:
        if structured_plan is not None:
            current_context.messages.append(message)
            ev_stream.push(AgentEventMessageEnd(message=message))
        new_messages.append(message)

    def schedule_structured_repair(
        error: Exception,
        candidate: AssistantMessage,
    ) -> bool:
        nonlocal structured_attempt, has_more_tool_calls, pending_validation_error
        if structured_plan is None or config.response_format is None:
            return False
        from openprogram.providers.structured_output import (
            StructuredOutputValidationError,
            build_repair_prompt,
        )

        if not isinstance(error, StructuredOutputValidationError):
            return False
        if structured_attempt > config.response_format.max_validation_retries:
            return False
        if inner_iterations >= iteration_cap:
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
        current_context.messages.extend([
            candidate,
            UserMessage(
                content=build_repair_prompt(error),
                timestamp=int(time.time() * 1000),
            ),
        ])
        structured_attempt = next_attempt
        pending_validation_error = error
        has_more_tool_calls = True
        return True

    # Hard cap on the inner tool-call loop so a model that keeps asking
    # for "one more tool call" can't churn the runtime forever. 50 is
    # plenty for a real task; anything beyond that is the model spinning.
    # A caller-set ``config.max_iterations`` (exec's ``max_iterations=``)
    # tightens the cap — it can never raise it past the hard limit.
    MAX_INNER_ITERATIONS = 50
    iteration_cap = MAX_INNER_ITERATIONS
    if config.max_iterations is not None:
        iteration_cap = max(1, min(MAX_INNER_ITERATIONS, config.max_iterations))
    inner_iterations = 0

    while True:
        # Turn boundary — pin the provider tools array for every call made
        # below. Tools that ``tool_search`` loads mid-turn stay out of the
        # array until the next boundary so the cached prefix (rooted on the
        # tools array) survives the turn; they are callable immediately via
        # the schema tool_search returns. See tool-toggle-management.md §6.
        from openprogram.programs import freeze_turn_tools
        freeze_turn_tools(list(current_context.tools or []))
        # Persist the priced provider/deferred split after freezing it. The
        # /context panel can then reproduce this HEAD even if the live tool
        # profile or MCP registry changes later.
        try:
            from openprogram.agent.session_db import default_db
            from openprogram.context.tool_snapshot_node import (
                record_tool_snapshot,
            )
            record_tool_snapshot(
                default_db(),
                config.session_id,
                list(current_context.tools or []),
            )
        except Exception:
            pass

        has_more_tool_calls = True
        steering_after_tools: list[AgentMessage] | None = None
        repeat_failures: dict[str, int] = {}

        while has_more_tool_calls or len(pending_messages) > 0:
            inner_iterations += 1
            if inner_iterations > iteration_cap:
                if pending_validation_error is not None:
                    raise pending_validation_error
                if structured_plan is not None and structured_plan.mode == "tool":
                    from openprogram.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    raise StructuredOutputValidationError(
                        "The model did not call the hidden structured-output submission tool",
                        code="missing_submission",
                    )
                # End the stream cleanly with whatever we've got. The
                # consumer (dispatcher / cli_chat) treats a normal
                # stream end as a successful turn — no more, no less.
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return
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

            if structured_plan is not None and message.stop_reason in (
                "length", "error", "aborted",
            ):
                from openprogram.providers.structured_output import (
                    StructuredOutputGenerationError,
                )

                if message.stop_reason == "aborted":
                    from openprogram.providers.utils.errors import ExecInterrupt
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
                raise StructuredOutputGenerationError(
                    "Structured output generation did not produce a complete value",
                    code=code,
                )

            if message.stop_reason in ("error", "aborted"):
                commit_assistant(message)
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            # Check for tool calls
            tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
            has_more_tool_calls = len(tool_calls) > 0

            if structured_plan is not None and structured_plan.mode == "tool":
                submit_calls = [
                    call
                    for call in tool_calls
                    if call.name == structured_plan.submit_tool_name
                ]
                if submit_calls and len(tool_calls) != 1:
                    from openprogram.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    error = StructuredOutputValidationError(
                        "The hidden structured-output submission must be the only tool call",
                        code="mixed_submission",
                    )
                    if schedule_structured_repair(error, message):
                        continue
                    raise error
                if submit_calls:
                    from openprogram.providers.structured_output import parse_and_validate_json

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
                        if schedule_structured_repair(error, message):
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
                    from openprogram.providers.structured_output import (
                        StructuredOutputValidationError,
                    )

                    error = StructuredOutputValidationError(
                        "The model did not call the hidden structured-output submission tool",
                        code="missing_submission",
                    )
                    if schedule_structured_repair(error, message):
                        continue
                    raise error

            if (
                structured_plan is not None
                and structured_plan.mode in ("native", "prompt")
                and not tool_calls
            ):
                from openprogram.providers.structured_output import parse_and_validate_json

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
                    if schedule_structured_repair(error, message):
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

            if steering_after_tools:
                pending_messages = steering_after_tools
                steering_after_tools = None
            else:
                pending_messages = []
                if config.get_steering_messages:
                    pending_messages = await config.get_steering_messages()

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


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    stream_fn: StreamFn | None,
    structured_plan: Any | None = None,
    provider_snapshot: Any | None = None,
    output_attempt: int | None = None,
) -> AssistantMessage:
    """
    Stream an assistant response from the LLM.
    Mirrors streamAssistantResponse() in TypeScript.
    """
    messages = context.messages

    # Apply context transform if configured
    if config.transform_context:
        messages = await config.transform_context(messages, cancel_event)

    # Convert to LLM-compatible messages
    convert = config.convert_to_llm
    if inspect.iscoroutinefunction(convert):
        llm_messages = await convert(messages)
    else:
        result = convert(messages)
        if inspect.isawaitable(result):
            llm_messages = await result
        else:
            llm_messages = result

    # Per-turn memory prefetch — extract the latest user message and ask the
    # memory subsystem for relevant snippets. The result is already fenced as
    # <memory-context>; it renders as a PREFIX BLOCK INSIDE the current user
    # message (dag/overview.md §7), never on the system prompt. Prefetch
    # changes with every new user input, so appending it to the system prompt
    # invalidated the provider's cached prefix for the ENTIRE history on every
    # turn — the single largest source of avoidable input cost. In the user
    # turn it only ever invalidates the tail it sits in.
    prefetch_block = context.memory_prefetch
    if prefetch_block is None:
        prefetch_block = ""
        latest_user_text = _latest_user_text(messages)
        if latest_user_text:
            try:
                from openprogram.memory import get_backend
                prefetch_block = get_backend().search(latest_user_text)
            except Exception:
                prefetch_block = ""

    sys_prompt = context.system_prompt or None
    if structured_plan is not None and structured_plan.mode == "prompt":
        from openprogram.providers.structured_output import build_prompt_fallback

        instruction = build_prompt_fallback(config.response_format)
        sys_prompt = f"{sys_prompt}\n\n{instruction}" if sys_prompt else instruction
    if prefetch_block:
        _inject_memory_prefetch(llm_messages, prefetch_block)

    # Build LLM context
    # Layer 6 (Claude Code shouldDefer): split the tools list into the
    # provider array. The split reads the turn-frozen set installed by
    # ``freeze_turn_tools`` at the turn boundary, so this returns the
    # SAME array on every call within a turn — the cached prefix rooted
    # on the tools array survives a mid-turn ``tool_search``.
    from openprogram.programs import split_tools_for_dispatch
    _provider_tools, _ = split_tools_for_dispatch(
        list(context.tools or [])
    )
    if structured_plan is not None and structured_plan.mode == "tool":
        _provider_tools = [
            *_provider_tools,
            Tool(
                name=structured_plan.submit_tool_name,
                description="Submit the final response matching the required schema.",
                parameters=structured_plan.provider_schema,
            ),
        ]
    llm_context = Context(
        system_prompt=sys_prompt,
        messages=llm_messages,
        tools=_provider_tools,
    )

    fn = stream_fn
    from openprogram.providers.api_registry import resolve_api_provider_snapshot

    if provider_snapshot is None:
        provider_snapshot = resolve_api_provider_snapshot(config.model)
    dispatch_snapshots = {id(config.model): provider_snapshot}
    dispatch_models = [config.model]

    # Provider/model failover — ON by default, conservatively.
    # resolve_fallback_models() defaults to the user's other enabled models of
    # the SAME provider (max 2), so failover never reaches a provider the user
    # has not configured; OPENPROGRAM_FALLBACK_MODELS overrides it with an
    # explicit (possibly cross-provider) list, and "off"/"none" disables it.
    # The chain only engages on a failover-worthy pre-content failure. Only the
    # default fn is wrapped (a caller-supplied stream_fn is left untouched);
    # wrapped in try/except so failover can never break the normal path.
    if stream_fn is None:
        from openprogram.providers.stream import stream_simple_with_provider

        def snapshot_stream(candidate, candidate_context, candidate_options):
            snapshot = dispatch_snapshots.get(id(candidate))
            provider = snapshot.provider if snapshot is not None else None
            return stream_simple_with_provider(
                provider,
                candidate,
                candidate_context,
                candidate_options,
                get_api_key=config.get_api_key,
            )

        fn = snapshot_stream
        try:
            from openprogram.providers.utils.failover import (
                resolve_fallback_models,
                failover_stream_fn,
            )
            _fallbacks = resolve_fallback_models(config.model)
            if structured_plan is not None:
                from openprogram.providers.api_registry import resolve_api_provider_snapshot
                from openprogram.providers.structured_output import negotiate_structured_output

                compatible = []
                for fallback in _fallbacks:
                    fallback_snapshot = resolve_api_provider_snapshot(fallback)
                    if fallback_snapshot.provider is None:
                        continue
                    try:
                        fallback_plan = negotiate_structured_output(
                            fallback,
                            fallback_snapshot.structured_output,
                            config.response_format,
                            list(context.tools or []),
                            tool_choice=config.tool_choice,
                            parallel_tool_calls=config.parallel_tool_calls,
                        )
                    except Exception:
                        continue
                    if (
                        fallback_plan.mode == structured_plan.mode
                        and fallback_plan.provider_schema == structured_plan.provider_schema
                    ):
                        compatible.append(fallback)
                        dispatch_snapshots[id(fallback)] = fallback_snapshot
                _fallbacks = compatible
            else:
                available = []
                for fallback in _fallbacks:
                    fallback_snapshot = resolve_api_provider_snapshot(fallback)
                    if fallback_snapshot.provider is None:
                        continue
                    available.append(fallback)
                    dispatch_snapshots[id(fallback)] = fallback_snapshot
                _fallbacks = available
            dispatch_models.extend(_fallbacks)
            if _fallbacks:
                fn = failover_stream_fn(fn, _fallbacks)
        except Exception:
            pass

    # The effect contract is established before the first provider dispatch.
    # A stable key is safe only when every candidate that can actually receive
    # this request advertises support for it. This remains conservative for an
    # explicit cross-provider chain with mixed capabilities: no candidate gets
    # a key, and the effect is recorded as nonrepeatable.
    provider_supports_idempotency_key = bool(
        dispatch_models
        and all(
            snapshot is not None and snapshot.supports_idempotency_key
            for snapshot in (
                dispatch_snapshots.get(id(candidate)) for candidate in dispatch_models
            )
        )
    )

    assert fn is not None

    # Resolve API key
    resolved_api_key = config.api_key
    if stream_fn is not None and config.get_api_key:
        key_result = config.get_api_key(config.model.provider)
        if inspect.isawaitable(key_result):
            key_result = await key_result
        resolved_api_key = key_result or resolved_api_key

    provider_response_format = None
    if structured_plan is not None and structured_plan.mode == "native":
        provider_response_format = replace(
            config.response_format,
            schema=structured_plan.provider_schema,
        )

    from openprogram.providers import SimpleStreamOptions
    stream_opts = SimpleStreamOptions(
        reasoning=config.reasoning,
        thinking_budgets=config.thinking_budgets,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        signal=cancel_event,
        api_key=resolved_api_key,
        transport=config.transport,
        cache_retention=config.cache_retention,
        session_id=config.session_id,
        on_payload=config.on_payload,
        headers=config.headers,
        max_retry_delay_ms=config.max_retry_delay_ms,
        metadata=config.metadata,
        service_tier=config.service_tier,
        tool_choice=config.tool_choice,
        parallel_tool_calls=(
            False
            if structured_plan is not None and structured_plan.mode == "tool"
            else config.parallel_tool_calls
        ),
        web_search=config.web_search,
        response_format=provider_response_format,
        supports_idempotency_key=provider_supports_idempotency_key,
    )

    partial_message: AssistantMessage | None = None
    added_partial = False

    if config.safe_point_hook is not None:
        from openprogram.agent.continuation import runtime_contract_snapshot

        resolved_snapshot = context.runtime_contract
        if resolved_snapshot is None:
            # Direct agent_loop callers do not have a TurnRequest.  Keep this
            # fallback only for non-durable callers; production continuation
            # always supplies the resolved request contract from the driver.
            resolved_snapshot = runtime_contract_snapshot(
                model=config.model,
                system_prompt=context.system_prompt,
                tools=context.tools,
                request=None,
                structured_output=config.response_format,
            )
        provider_payload = {
            "resolved_snapshot": resolved_snapshot,
            "supports_idempotency_key": provider_supports_idempotency_key,
            "dispatch_candidates": [
                {
                    "api": getattr(candidate, "api", None),
                    "provider": getattr(candidate, "provider", None),
                    "model": getattr(candidate, "id", None),
                    "supports_idempotency_key": bool(
                        snapshot is not None and snapshot.supports_idempotency_key
                    ),
                }
                for candidate in dispatch_models
                for snapshot in (dispatch_snapshots.get(id(candidate)),)
            ],
            "context": {
                "system_prompt": llm_context.system_prompt,
                "messages": [_durable_message(message) for message in llm_context.messages],
                "tools": [_durable_message(tool) for tool in (llm_context.tools or [])],
            },
        }
        await config.safe_point_hook("provider.before", provider_payload)
        if provider_payload.get("supports_idempotency_key") is True:
            stream_opts.idempotency_key = provider_payload.get("idempotency_key")
        else:
            stream_opts.idempotency_key = None
    _record_job_activity("operation_start")
    response_stream = fn(config.model, llm_context, stream_opts)

    iterator = response_stream.__aiter__()
    while True:
        if structured_plan is not None and cancel_event and cancel_event.is_set():
            from openprogram.providers.utils.errors import ExecInterrupt

            raise ExecInterrupt("cancelled")
        timeout = _job_operation_timeout(None)
        try:
            event = (
                await iterator.__anext__()
                if timeout is None
                else await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
            )
        except StopAsyncIteration:
            break
        _record_job_activity("provider_data")
        if structured_plan is not None and cancel_event and cancel_event.is_set():
            from openprogram.providers.utils.errors import ExecInterrupt

            raise ExecInterrupt("cancelled")
        if event.type == "start":
            partial_message = event.partial
            if structured_plan is None:
                context.messages.append(partial_message)
                added_partial = True
            ev_stream.push(AgentEventMessageStart(message=partial_message))
            emit_safe("model.response_started", "agent")

        elif event.type in (
            "text_start", "text_delta", "text_end",
            "thinking_start", "thinking_delta", "thinking_end",
            "toolcall_start", "toolcall_delta", "toolcall_end",
        ):
            if partial_message is not None:
                if output_attempt is not None and event.type in (
                    "text_start", "text_delta", "text_end",
                ):
                    event = event.model_copy(update={"output_attempt": output_attempt})
                partial_message = event.partial
                if added_partial:
                    context.messages[-1] = partial_message
                ev_stream.push(AgentEventMessageUpdate(
                    message=partial_message,
                    assistant_message_event=event,
                ))

        elif event.type in ("done", "error"):
            final_message = event.message if event.type == "done" else event.error
            if structured_plan is not None:
                if partial_message is None:
                    ev_stream.push(AgentEventMessageStart(message=final_message))
                emit_safe("model.response_completed", "agent",
                          {"is_error": event.type == "error"})
                return final_message
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                ev_stream.push(AgentEventMessageStart(message=final_message))
            ev_stream.push(AgentEventMessageEnd(message=final_message))
            emit_safe("model.response_completed", "agent",
                      {"is_error": event.type == "error"})
            return final_message

    if structured_plan is not None:
        from openprogram.providers.structured_output import (
            StructuredOutputGenerationError,
        )

        raise StructuredOutputGenerationError(
            "Structured output stream ended without a terminal event",
            code="incomplete",
        )

    # Ordinary text mode preserves the legacy partial-message fallback.
    if partial_message:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Request was aborted")
        return partial_message

    raise RuntimeError("Stream ended without a final message")


# In-flight tool executions, keyed by tool_call_id. Read by the worker's
# GET /api/running so the Running panel can show what the agent is
# executing right now (bash commands, code runs, sub-agents, …).
import threading as _threading

RUNNING_TOOL_CALLS: dict[str, dict] = {}
RUNNING_TOOL_CALLS_LOCK = _threading.Lock()


def _tool_call_label(name: str, args: Any) -> str:
    """Human-readable one-liner for a tool call (UI display only)."""
    if isinstance(args, dict):
        for key in ("description", "command", "prompt", "query", "path"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:200]
    return name


class _SkipExecute(Exception):
    """Internal: repeat-fail trip already built a tool result."""


def _tool_repeat_key(name: str, args: Any) -> str:
    try:
        blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        blob = repr(args)
    return f"{name}\0{blob}"


async def _execute_tool_calls(
    tools: list[AgentTool] | None,
    assistant_message: AssistantMessage,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    get_steering_messages: Any | None = None,
    repeat_failures: dict[str, int] | None = None,
    safe_point_hook: Any | None = None,
    *,
    start_index: int = 0,
) -> dict[str, Any]:
    """
    Execute tool calls from an assistant message.
    Mirrors executeToolCalls() in TypeScript.
    """
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCall)]
    results: list[ToolResultMessage] = []
    steering_messages: list[AgentMessage] | None = None
    stop_at_safe_point = False
    if repeat_failures is None:
        repeat_failures = {}

    from openprogram.context.cache_aware_microcompact import increment_tool_calls
    increment_tool_calls(len(tool_calls))

    for index, tool_call in enumerate(tool_calls[start_index:], start=start_index):
        tool = next((t for t in (tools or []) if t.name == tool_call.name), None)
        fail_key = _tool_repeat_key(tool_call.name, tool_call.arguments)
        streak = repeat_failures.get(fail_key, 0)

        ev_stream.push(AgentEventToolStart(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
        ))
        if safe_point_hook is not None:
            await safe_point_hook("tool.before", {
                "tool_call_id": str(tool_call.id), "tool_name": tool_call.name,
                "arguments": tool_call.arguments,
            })

        # 事件层：tool.before 一份事件，观察（异步总线）+ 问询（同步 gate）共用。
        # plugin 的 tool.before handler 就是 gate 订阅者（plugins/hooks.py）。
        before_ev = make_event("tool.before", "agent",
                               {"tool": tool_call.name,
                                "tool_call_id": tool_call.id,
                                "args": tool_call.arguments})
        try:
            get_event_bus().emit(before_ev)
        except Exception:
            pass
        gate_denial = decide_tool_gate(before_ev)

        # session/execution 由 run_control 的 contextvar 提供：session_id 让
        # /api/running 能按会话分组和跳转，execution_id 区分前台轮次和后台分支。
        from openprogram.agent.run_control import (
            get_current_session_id, get_current_execution_id,
        )
        with RUNNING_TOOL_CALLS_LOCK:
            RUNNING_TOOL_CALLS[tool_call.id] = {
                "tool_name": tool_call.name,
                "label": _tool_call_label(tool_call.name, tool_call.arguments),
                "started_at": time.time(),
                "session_id": get_current_session_id(),
                "execution_id": get_current_execution_id(),
            }

        result: AgentToolResult
        skipped_repeat = streak >= 2
        if skipped_repeat:
            n = streak + 1
            repeat_failures[fail_key] = n
            result = AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=(
                        f"你已连续 {n} 次重复同一失败调用，"
                        "请改变方法或向用户说明阻碍"
                    ),
                )],
                details={},
                is_error=True,
            )
        try:
            if skipped_repeat:
                raise _SkipExecute()
            if gate_denial is not None:
                raise ToolGateDenied(f"Tool call blocked: {gate_denial}")
            if not tool:
                raise ValueError(f"Tool {tool_call.name} not found")

            # Build a Tool-compatible object for validation
            from openprogram.providers.types import Tool as AiTool, ToolCall as AiToolCall
            ai_tool = AiTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
            validated_args = validate_tool_arguments(ai_tool, tool_call)

            def on_update(partial_result: AgentToolResult) -> None:
                _record_job_activity("tool_progress")
                ev_stream.push(AgentEventToolUpdate(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                    partial_result=partial_result,
                ))

            _record_job_activity("operation_start")
            timeout = _job_operation_timeout(None)
            operation = tool.execute(
                tool_call.id, validated_args, cancel_event, on_update,
            )
            result = (
                await operation
                if timeout is None
                else await asyncio.wait_for(operation, timeout=timeout)
            )
        except _SkipExecute:
            pass
        except Exception as e:
            result = AgentToolResult(
                content=[TextContent(type="text", text=str(e))],
                details={},
                is_error=True,
            )
        except BaseException as e:
            # User-triggered cancel (openprogram.agentic_programming.function.CancelledError
            # is a BaseException so user-written `except Exception` inside tool bodies
            # cannot swallow it). Push a tool_end event so the UI sees the call
            # closed, then re-raise to abort the agent loop. The outer _run handler
            # ends the event stream gracefully so the chat dispatcher unblocks and
            # `running_task` is cleared — without this the stop button keeps showing
            # because the asyncio Task is killed by an "unretrieved" BaseException
            # and the stream never terminates.
            result = AgentToolResult(
                content=[TextContent(type="text", text=f"Cancelled: {e}")],
                details={},
                is_error=True,
            )
            ev_stream.push(AgentEventToolEnd(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
                is_error=True,
            ))
            raise
        finally:
            with RUNNING_TOOL_CALLS_LOCK:
                RUNNING_TOOL_CALLS.pop(tool_call.id, None)

        if not skipped_repeat:
            if result.is_error:
                repeat_failures[fail_key] = streak + 1
            else:
                repeat_failures.pop(fail_key, None)

        ev_stream.push(AgentEventToolEnd(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            is_error=result.is_error,
        ))
        emit_safe("tool.after", "tool", {
            "tool": tool_call.name,
            "tool_call_id": tool_call.id,
            "is_error": result.is_error,
            # Only the text channel of the result — binary attachments
            # can be huge and rarely useful for subscribers.
            "result_text": "".join(
                c.text for c in (result.content or [])
                if hasattr(c, "text") and isinstance(c.text, str)
            ),
        })

        tool_result_msg = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=result.content,
            details=result.details,
            is_error=result.is_error,
            timestamp=int(time.time() * 1000),
        )
        if safe_point_hook is not None:
            stop_at_safe_point = bool(await safe_point_hook("tool.after", {
                "tool_call_id": str(tool_call.id), "tool_name": tool_call.name,
                "result": _durable_message(tool_result_msg),
                "is_error": bool(result.is_error),
                "next_tool_index": index + 1,
                "repeat_failures": dict(repeat_failures),
                "tool_call_ids": [str(call.id) for call in tool_calls],
            }))
        results.append(tool_result_msg)
        ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
        ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

        if stop_at_safe_point:
            break

        # Check for steering messages after each tool execution
        if get_steering_messages:
            steering = await get_steering_messages()
            if steering:
                steering_messages = steering
                # Skip remaining tool calls
                remaining = tool_calls[index + 1:]
                for skipped in remaining:
                    results.append(_skip_tool_call(skipped, ev_stream))
                break

    return {
        "tool_results": results, "steering_messages": steering_messages,
        "stop_at_safe_point": stop_at_safe_point,
    }


def _skip_tool_call(
    tool_call: ToolCall,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
) -> ToolResultMessage:
    """Create a skipped tool result. Mirrors skipToolCall() in TypeScript."""
    result = AgentToolResult(
        content=[TextContent(type="text", text="Skipped due to queued user message.")],
        details={},
        is_error=True,
    )

    ev_stream.push(AgentEventToolStart(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        args=tool_call.arguments,
    ))
    ev_stream.push(AgentEventToolEnd(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result=result,
        is_error=True,
    ))

    tool_result_msg = ToolResultMessage(
        role="toolResult",
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=result.content,
        details={},
        is_error=True,
        timestamp=int(time.time() * 1000),
    )
    ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
    ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

    return tool_result_msg
