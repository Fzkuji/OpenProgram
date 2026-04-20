"""
Agent class — mirrors packages/agent/src/agent.ts

Stateful wrapper around the agent loop.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable

from openprogram.providers import get_model, stream_simple
from openprogram.providers.types import (
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingBudgets,
    ToolResultMessage,
    Transport,
    Usage,
    UserMessage,
)

from .agent_loop import agent_loop, agent_loop_continue
from .types import (
    AgentContext,
    AgentEvent,
    AgentEventAgentEnd,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    AgentTool,
    StreamFn,
    ThinkingLevel,
)


def _default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Default converter: keep only LLM-compatible messages."""
    return [
        m for m in messages
        if hasattr(m, "role") and m.role in ("user", "assistant", "toolResult")
    ]


class AgentOptions:
    """Options for constructing an Agent. Mirrors AgentOptions interface."""

    def __init__(
        self,
        initial_state: dict[str, Any] | None = None,
        convert_to_llm: Callable[[list[AgentMessage]], list[Message]] | None = None,
        transform_context: Callable | None = None,
        steering_mode: str = "one-at-a-time",
        follow_up_mode: str = "one-at-a-time",
        stream_fn: StreamFn | None = None,
        session_id: str | None = None,
        get_api_key: Callable | None = None,
        on_payload: Callable[[Any, Model], Any | None] | None = None,
        thinking_budgets: ThinkingBudgets | None = None,
        transport: Transport = "sse",
        max_retry_delay_ms: int | None = None,
    ):
        self.initial_state = initial_state
        self.convert_to_llm = convert_to_llm
        self.transform_context = transform_context
        self.steering_mode = steering_mode
        self.follow_up_mode = follow_up_mode
        self.stream_fn = stream_fn
        self.session_id = session_id
        self.get_api_key = get_api_key
        self.on_payload = on_payload
        self.thinking_budgets = thinking_budgets
        self.transport = transport
        self.max_retry_delay_ms = max_retry_delay_ms

    @classmethod
    def from_dict(cls, opts_dict: dict[str, Any]) -> "AgentOptions":
        """Construct AgentOptions from a dict (mirrors TS object literal usage)."""
        return cls(**opts_dict)


class Agent:
    """
    Stateful agent wrapper around the agent loop.
    Mirrors the Agent class in TypeScript.
    """

    def __init__(self, opts: AgentOptions | dict[str, Any] | None = None) -> None:
        # Support dict (like TS object literal) or AgentOptions instance
        if isinstance(opts, dict):
            opts = AgentOptions.from_dict(opts)
        opts = opts or AgentOptions()

        default_model = get_model("google", "gemini-2.5-flash-lite-preview-06-17")

        self._state = AgentState(
            system_prompt="",
            model=default_model,
            thinking_level="off",
            tools=[],
            messages=[],
            is_streaming=False,
            stream_message=None,
            pending_tool_calls=set(),
            error=None,
        )

        if opts.initial_state:
            for key, value in opts.initial_state.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)

        self._convert_to_llm = opts.convert_to_llm or _default_convert_to_llm
        self._transform_context = opts.transform_context
        self._steering_mode: str = opts.steering_mode
        self._follow_up_mode: str = opts.follow_up_mode
        self.stream_fn: StreamFn = opts.stream_fn or stream_simple
        self._session_id: str | None = opts.session_id
        self.get_api_key = opts.get_api_key
        self._on_payload = opts.on_payload
        self._thinking_budgets: ThinkingBudgets | None = opts.thinking_budgets
        self._transport: Transport = opts.transport
        self._max_retry_delay_ms: int | None = opts.max_retry_delay_ms

        self._listeners: set[Callable[[AgentEvent], None]] = set()
        self._cancel_event: asyncio.Event | None = None
        self._steering_queue: list[AgentMessage] = []
        self._follow_up_queue: list[AgentMessage] = []
        self._running_task: asyncio.Task | None = None

        # TS parity: expose `agent.continue()` despite Python keyword constraints.
        setattr(self, "continue", self.continue_from_context)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._session_id = value

    @property
    def thinking_budgets(self) -> ThinkingBudgets | None:
        return self._thinking_budgets

    @thinking_budgets.setter
    def thinking_budgets(self, value: ThinkingBudgets | None) -> None:
        self._thinking_budgets = value

    @property
    def transport(self) -> Transport:
        return self._transport

    def set_transport(self, value: Transport) -> None:
        self._transport = value

    @property
    def max_retry_delay_ms(self) -> int | None:
        return self._max_retry_delay_ms

    @max_retry_delay_ms.setter
    def max_retry_delay_ms(self, value: int | None) -> None:
        self._max_retry_delay_ms = value

    @property
    def state(self) -> AgentState:
        return self._state

    # ── Subscriptions ─────────────────────────────────────────────────────────

    def subscribe(self, fn: Callable[[AgentEvent], None]) -> Callable[[], None]:
        """Subscribe to agent events. Returns unsubscribe function."""
        self._listeners.add(fn)
        return lambda: self._listeners.discard(fn)

    def _emit(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            listener(event)

    # ── State mutators ────────────────────────────────────────────────────────

    def set_system_prompt(self, v: str) -> None:
        self._state.system_prompt = v

    def set_model(self, m: Model) -> None:
        self._state.model = m

    def set_thinking_level(self, level: ThinkingLevel) -> None:
        self._state.thinking_level = level

    def set_steering_mode(self, mode: str) -> None:
        self._steering_mode = mode

    def get_steering_mode(self) -> str:
        return self._steering_mode

    def set_follow_up_mode(self, mode: str) -> None:
        self._follow_up_mode = mode

    def get_follow_up_mode(self) -> str:
        return self._follow_up_mode

    def set_tools(self, tools: list[AgentTool]) -> None:
        self._state.tools = tools

    def replace_messages(self, messages: list[AgentMessage]) -> None:
        self._state.messages = list(messages)

    def append_message(self, message: AgentMessage) -> None:
        self._state.messages = [*self._state.messages, message]

    def clear_messages(self) -> None:
        self._state.messages = []

    def steer(self, message: AgentMessage) -> None:
        """Queue a steering message to interrupt the agent mid-run."""
        self._steering_queue.append(message)

    def follow_up(self, message: AgentMessage) -> None:
        """Queue a follow-up message to be processed after the agent finishes."""
        self._follow_up_queue.append(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue = []

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue = []

    def clear_all_queues(self) -> None:
        self._steering_queue = []
        self._follow_up_queue = []

    def has_queued_messages(self) -> bool:
        return bool(self._steering_queue) or bool(self._follow_up_queue)

    def _dequeue_steering_messages(self) -> list[AgentMessage]:
        if self._steering_mode == "one-at-a-time":
            if self._steering_queue:
                first = self._steering_queue[0]
                self._steering_queue = self._steering_queue[1:]
                return [first]
            return []
        msgs = list(self._steering_queue)
        self._steering_queue = []
        return msgs

    async def _async_dequeue_follow_up(self) -> list[AgentMessage]:
        return self._dequeue_follow_up_messages()

    def _dequeue_follow_up_messages(self) -> list[AgentMessage]:
        if self._follow_up_mode == "one-at-a-time":
            if self._follow_up_queue:
                first = self._follow_up_queue[0]
                self._follow_up_queue = self._follow_up_queue[1:]
                return [first]
            return []
        msgs = list(self._follow_up_queue)
        self._follow_up_queue = []
        return msgs

    def abort(self) -> None:
        """Cancel the current operation."""
        if self._cancel_event:
            self._cancel_event.set()
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()

    async def wait_for_idle(self) -> None:
        """Wait until the agent is done processing."""
        if self._running_task and not self._running_task.done():
            try:
                await self._running_task
            except (asyncio.CancelledError, Exception):
                pass

    def reset(self) -> None:
        self._state.messages = []
        self._state.is_streaming = False
        self._state.stream_message = None
        self._state.pending_tool_calls = set()
        self._state.error = None
        self._steering_queue = []
        self._follow_up_queue = []

    # ── Prompt / Continue ─────────────────────────────────────────────────────

    async def prompt(
        self,
        input: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
    ) -> None:
        """Send a prompt to the agent."""
        if self._state.is_streaming:
            raise RuntimeError(
                "Agent is already processing a prompt. Use steer() or follow_up() to queue messages."
            )

        if not self._state.model:
            raise RuntimeError("No model configured")

        msgs: list[AgentMessage]

        if isinstance(input, list):
            msgs = input
        elif isinstance(input, str):
            content: list[TextContent | ImageContent] = [TextContent(type="text", text=input)]
            if images:
                content.extend(images)
            msgs = [UserMessage(
                role="user",
                content=content,
                timestamp=int(time.time() * 1000),
            )]
        else:
            msgs = [input]

        await self._run_loop(msgs)

    async def continue_from_context(self) -> None:
        """Continue from current context (for retries)."""
        if self._state.is_streaming:
            raise RuntimeError("Agent is already processing.")

        messages = self._state.messages
        if not messages:
            raise RuntimeError("No messages to continue from")

        last = messages[-1]
        if hasattr(last, "role") and last.role == "assistant":
            # Try to continue with queued messages
            queued = self._dequeue_steering_messages()
            if queued:
                await self._run_loop(queued, skip_initial_steering_poll=True)
                return

            queued_follow = self._dequeue_follow_up_messages()
            if queued_follow:
                await self._run_loop(queued_follow)
                return

            raise RuntimeError("Cannot continue from message role: assistant")

        await self._run_loop(None)

    async def _run_loop(
        self,
        messages: list[AgentMessage] | None,
        skip_initial_steering_poll: bool = False,
    ) -> None:
        """Run the agent loop."""
        model = self._state.model
        if not model:
            raise RuntimeError("No model configured")

        self._cancel_event = asyncio.Event()
        self._state.is_streaming = True
        self._state.stream_message = None
        self._state.error = None

        reasoning = None if self._state.thinking_level == "off" else self._state.thinking_level

        context = AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=self._state.tools,
        )

        _skip_initial = skip_initial_steering_poll

        async def get_steering() -> list[AgentMessage]:
            nonlocal _skip_initial
            if _skip_initial:
                _skip_initial = False
                return []
            return self._dequeue_steering_messages()

        config = AgentLoopConfig(
            model=model,
            reasoning=reasoning,
            session_id=self._session_id,
            transport=self._transport,
            thinking_budgets=self._thinking_budgets,
            max_retry_delay_ms=self._max_retry_delay_ms,
            convert_to_llm=self._convert_to_llm,
            transform_context=self._transform_context,
            get_api_key=self.get_api_key,
            on_payload=self._on_payload,
            get_steering_messages=get_steering,
            get_follow_up_messages=self._async_dequeue_follow_up,
        )

        partial: AgentMessage | None = None

        try:
            if messages is not None:
                ev_stream = agent_loop(messages, context, config, self._cancel_event, self.stream_fn)
            else:
                ev_stream = agent_loop_continue(context, config, self._cancel_event, self.stream_fn)

            self._running_task = asyncio.current_task()

            async for event in ev_stream:
                # Update internal state
                if event.type == "message_start":
                    partial = event.message
                    self._state.stream_message = event.message

                elif event.type == "message_update":
                    partial = event.message
                    self._state.stream_message = event.message

                elif event.type == "message_end":
                    partial = None
                    self._state.stream_message = None
                    self.append_message(event.message)

                elif event.type == "tool_execution_start":
                    s = set(self._state.pending_tool_calls)
                    s.add(event.tool_call_id)
                    self._state.pending_tool_calls = s

                elif event.type == "tool_execution_end":
                    s = set(self._state.pending_tool_calls)
                    s.discard(event.tool_call_id)
                    self._state.pending_tool_calls = s

                elif event.type == "turn_end":
                    if hasattr(event.message, "error_message") and event.message.error_message:
                        self._state.error = event.message.error_message

                elif event.type == "agent_end":
                    self._state.is_streaming = False
                    self._state.stream_message = None

                self._emit(event)

            # Handle remaining partial
            if partial and hasattr(partial, "role") and partial.role == "assistant":
                only_empty = not any(
                    (hasattr(c, "thinking") and c.thinking.strip()) or
                    (hasattr(c, "text") and c.text.strip()) or
                    (hasattr(c, "name") and c.name.strip())
                    for c in getattr(partial, "content", [])
                )
                if not only_empty:
                    self.append_message(partial)
                else:
                    if self._cancel_event and self._cancel_event.is_set():
                        raise RuntimeError("Request was aborted")

        except Exception as err:
            is_aborted = self._cancel_event.is_set() if self._cancel_event else False
            error_msg = AssistantMessage(
                role="assistant",
                content=[TextContent(type="text", text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=Usage(),
                stop_reason="aborted" if is_aborted else "error",
                error_message=str(err),
                timestamp=int(time.time() * 1000),
            )
            self.append_message(error_msg)
            self._state.error = str(err)
            self._emit(AgentEventAgentEnd(messages=[error_msg]))
        finally:
            self._state.is_streaming = False
            self._state.stream_message = None
            self._state.pending_tool_calls = set()
            self._cancel_event = None
            self._running_task = None
