"""
AgentSession — lightweight stateful wrapper around Agent with auto-retry.

What this class provides:
  - Construction: model + tools + system_prompt + thinking_level
  - Auto-retry with exponential backoff on transient errors
  - Event subscription via an EventBus
  - Manual messages injection (Runtime owns the context, we just run a turn)

What this class deliberately does NOT do (vs. pi_coding_agent's AgentSession):
  - No session persistence to disk
  - No auth/settings/model-profile management
  - No built-in tools
  - No auto-compaction trigger (call compact() explicitly if desired)
  - No queue/steering/follow-up orchestration (use Agent directly for that)

The Runtime layer owns context management, compaction policy, and persistence.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from openprogram.providers.types import (
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    TextContent,
    UserMessage,
)

from .agent import Agent, AgentOptions
from .event_bus import EventBus
from .messages import wrap_convert_to_llm
from .retry import (
    DEFAULT_RETRY_SETTINGS,
    RetrySettings,
    compute_backoff_ms,
    is_retryable_error,
)
from .types import AgentMessage, AgentTool, ThinkingLevel


class AgentSession:
    """Run a single-turn agent loop with auto-retry on transient errors.

    Typical usage from Runtime:

        session = AgentSession(model, tools=tools, system_prompt=...)
        if initial_messages:
            session.replace_messages(initial_messages)
        final = await session.run("user input")
        # Runtime pulls updated messages back:
        updated_messages = session.messages
    """

    def __init__(
        self,
        model: Model,
        tools: list[AgentTool] | None = None,
        system_prompt: str = "",
        thinking_level: ThinkingLevel = "off",
        api_key: str | None = None,
        retry: RetrySettings | None = None,
        event_bus: EventBus | None = None,
        block_images: bool = False,
        initial_messages: list[AgentMessage] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._retry = retry or DEFAULT_RETRY_SETTINGS
        self._event_bus = event_bus
        self._retry_attempt = 0

        initial_state: dict[str, Any] = {
            "model": model,
            "system_prompt": system_prompt,
            "thinking_level": thinking_level,
            "tools": list(tools or []),
        }
        if initial_messages:
            initial_state["messages"] = list(initial_messages)

        get_api_key: Callable[[str], str] | None = None
        if api_key:
            _k = api_key
            get_api_key = lambda _provider: _k  # noqa: E731

        self._agent = Agent(AgentOptions(
            initial_state=initial_state,
            convert_to_llm=wrap_convert_to_llm(block_images),
            get_api_key=get_api_key,
            session_id=session_id,
        ))

        if event_bus is not None:
            self._unsubscribe = self._agent.subscribe(
                lambda ev: event_bus.emit("agent", ev)
            )
        else:
            self._unsubscribe = None

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def agent(self) -> Agent:
        """Underlying Agent, in case the caller needs fine-grained control."""
        return self._agent

    @property
    def messages(self) -> list[AgentMessage]:
        """Current messages in the agent state."""
        return list(self._agent.state.messages)

    @property
    def last_assistant(self) -> AssistantMessage | None:
        """Most recent assistant message, or None if there isn't one yet."""
        for m in reversed(self._agent.state.messages):
            if getattr(m, "role", "") == "assistant":
                return m  # type: ignore[return-value]
        return None

    # ── State mutators (pass-through to Agent) ────────────────────────────────

    def replace_messages(self, messages: list[AgentMessage]) -> None:
        """Overwrite the agent's messages. Runtime uses this to inject its own context."""
        self._agent.replace_messages(messages)

    def set_tools(self, tools: list[AgentTool]) -> None:
        self._agent.set_tools(tools)

    def set_system_prompt(self, prompt: str) -> None:
        self._agent.set_system_prompt(prompt)

    def set_model(self, model: Model) -> None:
        self._agent.set_model(model)

    def set_thinking_level(self, level: ThinkingLevel) -> None:
        self._agent.set_thinking_level(level)

    # ── Run with retry ────────────────────────────────────────────────────────

    async def run(
        self,
        user_input: str | AgentMessage | list[AgentMessage] | None = None,
        images: list[ImageContent] | None = None,
    ) -> AssistantMessage | None:
        """Run the agent loop for one turn, with auto-retry on transient errors.

        If ``user_input`` is given, it's sent as a new prompt. If None, continues
        from current context (useful when messages have already been injected).

        Returns the final assistant message, or None if the run produced none.
        """
        if user_input is not None:
            initial = lambda: self._agent.prompt(user_input, images=images)  # noqa: E731
        else:
            initial = self._agent.continue_from_context

        await self._run_with_retry(initial)
        return self.last_assistant

    async def _run_with_retry(self, run_fn: Callable[[], Any]) -> None:
        """Internal: run once, then retry on transient errors."""
        attempt = 0
        current_fn = run_fn

        while True:
            await current_fn()

            last = self.last_assistant
            if last is None:
                return

            context_window = getattr(self._agent.state.model, "context_window", 0) or 0
            if not is_retryable_error(last, context_window):
                return

            if not self._retry.enabled or attempt >= self._retry.max_retries:
                self._emit({
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": attempt,
                    "final_error": getattr(last, "error_message", "Unknown error"),
                })
                return

            attempt += 1
            self._retry_attempt = attempt
            delay_ms = compute_backoff_ms(attempt, self._retry.base_delay_ms)

            self._emit({
                "type": "auto_retry_start",
                "attempt": attempt,
                "max_attempts": self._retry.max_retries,
                "delay_ms": delay_ms,
                "error_message": getattr(last, "error_message", "Unknown error"),
            })

            msgs = self._agent.state.messages
            if msgs and getattr(msgs[-1], "role", "") == "assistant":
                self._agent.replace_messages(msgs[:-1])

            try:
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                self._emit({
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": attempt,
                    "final_error": "Retry cancelled",
                })
                return

            current_fn = self._agent.continue_from_context

    # ── Convenience ───────────────────────────────────────────────────────────

    def abort(self) -> None:
        """Abort the currently-running turn, if any."""
        self._agent.abort()

    async def wait_for_idle(self) -> None:
        """Wait until the agent finishes its current run."""
        await self._agent.wait_for_idle()

    def reset(self) -> None:
        """Clear messages and streaming state. Preserves model/tools/system_prompt."""
        self._agent.reset()
        self._retry_attempt = 0

    def close(self) -> None:
        """Unsubscribe from the event bus, if subscribed."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _emit(self, data: dict[str, Any]) -> None:
        if self._event_bus is not None:
            self._event_bus.emit("session", data)

    # ── Context manager support ───────────────────────────────────────────────

    def __enter__(self) -> "AgentSession":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
