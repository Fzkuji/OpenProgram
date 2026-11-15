"""Public entry points: agent_loop(), agent_loop_continue() and agent_loop_resume()."""
from __future__ import annotations

import asyncio
from typing import Any

from openprogram.models.providers.types import ToolCall
from openprogram.models.providers.utils.event_stream import EventStream
from openprogram.runtime.agent.loop.types import (
    AgentContext,
    AgentEvent,
    AgentEventAgentEnd,
    AgentEventAgentStart,
    AgentEventMessageEnd,
    AgentEventMessageStart,
    AgentEventTurnStart,
    AgentLoopConfig,
    AgentMessage,
    StreamFn,
)

from openprogram.runtime.agent.loop.lifecycle import (
    _attach_cancellable_producer,
    _create_agent_stream,
    _finish_interrupted_stream,
)
from openprogram.runtime.agent.loop.tool_dispatch import _execute_tool_calls
from openprogram.runtime.agent.loop.loop import _run_loop


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
                run_note=context.run_note,
                current_date=context.current_date,
                turn_notes=context.turn_notes,
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
            _finish_interrupted_stream(ev_stream, e, new_messages, cancel_event)

    _attach_cancellable_producer(ev_stream, _run(), cancel_event)
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
                run_note=context.run_note,
                current_date=context.current_date,
                turn_notes=context.turn_notes,
                runtime_contract=context.runtime_contract,
            )

            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            await _run_loop(current_context, new_messages, config, cancel_event, ev_stream, stream_fn)
        except Exception as e:
            if not ev_stream._result_event.is_set():
                ev_stream.fail(e)
        except BaseException as e:
            _finish_interrupted_stream(ev_stream, e, new_messages, cancel_event)

    _attach_cancellable_producer(ev_stream, _run(), cancel_event)
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
                run_note=context.run_note,
                current_date=context.current_date,
                turn_notes=context.turn_notes,
                runtime_contract=context.runtime_contract,
            )
            assistant = continuation.assistant_message
            current_context.messages.append(assistant)
            ev_stream.push(AgentEventAgentStart())
            ev_stream.push(AgentEventTurnStart())

            tool_calls = [
                call for call in assistant.content if isinstance(call, ToolCall)
            ]
            if continuation.phase not in {"after_provider", "after_tool"}:
                raise ValueError("unsupported Agent continuation phase")
            # Results the checkpoint already holds precede the resumed suffix.
            # A wait before a later call in the same decision is an
            # after_provider point that still carries the earlier calls'
            # results; dropping them showed the model those calls as lost.
            current_context.messages.extend(continuation.tool_results)
            current_context.messages.extend(continuation.steer_messages())

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
                    context_window=getattr(config.model, "context_window", None),
                    tool_output_weight=getattr(config.model, "tool_output_weight", None),
                    history=current_context.messages,
                )
                tool_results = execution["tool_results"]
                current_context.messages.extend(tool_results)
                new_messages.extend(tool_results)
                # The tool loop already consumed these durable steering
                # inputs. Carry them into the next provider context rather
                # than polling the now-empty queue and losing the messages.
                steering = execution.get("steering_messages") or []
                for message in steering:
                    ev_stream.push(AgentEventMessageStart(message=message))
                    ev_stream.push(AgentEventMessageEnd(message=message))
                current_context.messages.extend(steering)
                new_messages.extend(steering)
                if execution.get("stop_at_safe_point"):
                    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                    ev_stream.end(new_messages)
                    return

            # A completed provider answer with no tool calls is itself the
            # terminal assistant result.  It must be persisted/finalized by
            # the continuation dispatcher, not sent through a second provider
            # request.
            if continuation.phase == "after_provider" and not tool_calls:
                steering = await config.get_steering_messages() if config.get_steering_messages else []
                if not steering:
                    new_messages.append(assistant)
                    ev_stream.push(AgentEventAgentEnd(messages=new_messages))
                    ev_stream.end(new_messages)
                    return
                for message in steering:
                    ev_stream.push(AgentEventMessageStart(message=message))
                    ev_stream.push(AgentEventMessageEnd(message=message))
                current_context.messages.extend(steering)
                new_messages.extend(steering)

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
            _finish_interrupted_stream(ev_stream, exc, new_messages, cancel_event)

    _attach_cancellable_producer(ev_stream, _run(), cancel_event)
    return ev_stream
