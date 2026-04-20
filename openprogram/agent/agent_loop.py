"""
Agent loop — mirrors packages/agent/src/agent-loop.ts

Core loop logic: agentLoop(), agentLoopContinue(), runLoop().
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, AsyncGenerator

from openprogram.providers import stream_simple as _default_stream_simple
from openprogram.providers.types import (
    AssistantMessage,
    Context,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.validation import validate_tool_arguments

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


def _create_agent_stream() -> EventStream[AgentEvent, list[AgentMessage]]:
    return EventStream(
        is_done=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages if e.type == "agent_end" else [],
    )


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

    asyncio.ensure_future(_run())
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
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)

    asyncio.ensure_future(_run())
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

    while True:
        has_more_tool_calls = True
        steering_after_tools: list[AgentMessage] | None = None

        while has_more_tool_calls or len(pending_messages) > 0:
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
                current_context, config, cancel_event, ev_stream, stream_fn
            )
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                ev_stream.push(AgentEventTurnEnd(message=message, tool_results=[]))
                ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                ev_stream.end(new_messages)
                return

            # Check for tool calls
            tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
            has_more_tool_calls = len(tool_calls) > 0

            tool_results: list[ToolResultMessage] = []
            if has_more_tool_calls:
                execution = await _execute_tool_calls(
                    current_context.tools,
                    message,
                    cancel_event,
                    ev_stream,
                    config.get_steering_messages,
                )
                tool_results.extend(execution["tool_results"])
                steering_after_tools = execution.get("steering_messages")

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

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

    # Build LLM context
    llm_context = Context(
        system_prompt=context.system_prompt or None,
        messages=llm_messages,
        tools=[t for t in (context.tools or [])],
    )

    fn = stream_fn or _default_stream_simple

    # Resolve API key
    resolved_api_key = config.api_key
    if config.get_api_key:
        key_result = config.get_api_key(config.model.provider)
        if inspect.isawaitable(key_result):
            key_result = await key_result
        resolved_api_key = key_result or resolved_api_key

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
    )

    partial_message: AssistantMessage | None = None
    added_partial = False

    response_stream = fn(config.model, llm_context, stream_opts)

    async for event in response_stream:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            ev_stream.push(AgentEventMessageStart(message=partial_message))

        elif event.type in (
            "text_start", "text_delta", "text_end",
            "thinking_start", "thinking_delta", "thinking_end",
            "toolcall_start", "toolcall_delta", "toolcall_end",
        ):
            if partial_message is not None:
                partial_message = event.partial
                context.messages[-1] = partial_message
                ev_stream.push(AgentEventMessageUpdate(
                    message=partial_message,
                    assistant_message_event=event,
                ))

        elif event.type in ("done", "error"):
            final_message = event.message if event.type == "done" else event.error
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                ev_stream.push(AgentEventMessageStart(message=final_message))
            ev_stream.push(AgentEventMessageEnd(message=final_message))
            return final_message

    # Fallback: return partial if no done/error event
    if partial_message:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Request was aborted")
        return partial_message

    raise RuntimeError("Stream ended without a final message")


async def _execute_tool_calls(
    tools: list[AgentTool] | None,
    assistant_message: AssistantMessage,
    cancel_event: asyncio.Event | None,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
    get_steering_messages: Any | None = None,
) -> dict[str, Any]:
    """
    Execute tool calls from an assistant message.
    Mirrors executeToolCalls() in TypeScript.
    """
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCall)]
    results: list[ToolResultMessage] = []
    steering_messages: list[AgentMessage] | None = None

    for index, tool_call in enumerate(tool_calls):
        tool = next((t for t in (tools or []) if t.name == tool_call.name), None)

        ev_stream.push(AgentEventToolStart(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
        ))

        result: AgentToolResult
        is_error = False

        try:
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
                ev_stream.push(AgentEventToolUpdate(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                    partial_result=partial_result,
                ))

            result = await tool.execute(tool_call.id, validated_args, cancel_event, on_update)
        except Exception as e:
            result = AgentToolResult(
                content=[TextContent(type="text", text=str(e))],
                details={},
            )
            is_error = True

        ev_stream.push(AgentEventToolEnd(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            is_error=is_error,
        ))

        tool_result_msg = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=result.content,
            details=result.details,
            is_error=is_error,
            timestamp=int(time.time() * 1000),
        )
        results.append(tool_result_msg)
        ev_stream.push(AgentEventMessageStart(message=tool_result_msg))
        ev_stream.push(AgentEventMessageEnd(message=tool_result_msg))

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

    return {"tool_results": results, "steering_messages": steering_messages}


def _skip_tool_call(
    tool_call: ToolCall,
    ev_stream: EventStream[AgentEvent, list[AgentMessage]],
) -> ToolResultMessage:
    """Create a skipped tool result. Mirrors skipToolCall() in TypeScript."""
    result = AgentToolResult(
        content=[TextContent(type="text", text="Skipped due to queued user message.")],
        details={},
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
