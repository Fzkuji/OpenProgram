"""Scripted ApiProvider for tests that need the normal provider path."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, AsyncGenerator, Literal

from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    EventDone,
    EventError,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventToolCallDelta,
    EventThinkingStart,
    EventToolCallEnd,
    EventToolCallStart,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
)


@dataclass(frozen=True)
class ScriptedText:
    text: str


@dataclass(frozen=True)
class ScriptedThinking:
    thinking: str


@dataclass(frozen=True)
class ScriptedToolCall:
    name: str
    arguments: dict[str, Any]
    id: str


@dataclass(frozen=True)
class ScriptedError:
    message: str
    event_reason: Literal["error", "aborted"] = "error"
    error_reason: str | None = None
    retryable: bool | None = None
    retry_after_s: float | None = None


ScriptedStep = ScriptedText | ScriptedThinking | ScriptedToolCall | ScriptedError


@dataclass(frozen=True)
class ScriptedCall:
    model: Model
    context: Context
    options: StreamOptions | None


class ScriptedProvider:
    """Yield one ordered scripted response for each provider call."""

    def __init__(self) -> None:
        self._responses: list[tuple[ScriptedStep, ...]] = []
        self.calls: list[ScriptedCall] = []
        self.call_count = 0

    def add_response(self, *steps: ScriptedStep) -> None:
        self._responses.append(steps)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        return self.stream_simple(model, context, options)

    async def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | StreamOptions | None = None,
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        if self.call_count >= len(self._responses):
            raise AssertionError("scripted provider received more calls than scripted responses")
        steps = self._responses[self.call_count]
        self.call_count += 1
        self.calls.append(ScriptedCall(
            model=model.model_copy(deep=True),
            context=context.model_copy(deep=True),
            options=options.model_copy(deep=True) if options is not None else None,
        ))

        content = []

        def message(
            *,
            stop_reason: str = "stop",
            error_message: str | None = None,
            error_reason: str | None = None,
            error_retryable: bool | None = None,
            error_retry_after_s: float | None = None,
        ) -> AssistantMessage:
            return AssistantMessage(
                content=list(content),
                api=model.api,
                provider=model.provider,
                model=model.id,
                stop_reason=stop_reason,
                error_message=error_message,
                error_reason=error_reason,
                error_retryable=error_retryable,
                error_retry_after_s=error_retry_after_s,
                timestamp=0,
            )

        yield EventStart(partial=message())
        for step in steps:
            index = len(content)
            if isinstance(step, ScriptedText):
                content.append(TextContent(text=""))
                yield EventTextStart(content_index=index, partial=message())
                content[index] = TextContent(text=step.text)
                yield EventTextDelta(content_index=index, delta=step.text, partial=message())
                yield EventTextEnd(content_index=index, content=step.text, partial=message())
            elif isinstance(step, ScriptedThinking):
                content.append(ThinkingContent(thinking=""))
                yield EventThinkingStart(content_index=index, partial=message())
                content[index] = ThinkingContent(thinking=step.thinking)
                yield EventThinkingDelta(content_index=index, delta=step.thinking, partial=message())
                yield EventThinkingEnd(content_index=index, content=step.thinking, partial=message())
            elif isinstance(step, ScriptedToolCall):
                tool_call = ToolCall(id=step.id, name=step.name, arguments=step.arguments)
                content.append(ToolCall(id=step.id, name=step.name, arguments={}))
                yield EventToolCallStart(content_index=index, partial=message())
                content[index] = tool_call
                yield EventToolCallDelta(
                    content_index=index,
                    delta=json.dumps(step.arguments),
                    partial=message(),
                )
                yield EventToolCallEnd(
                    content_index=index, tool_call=tool_call, partial=message()
                )
            else:
                yield EventError(
                    reason=step.event_reason,
                    error=message(
                        stop_reason=step.event_reason,
                        error_message=step.message,
                        error_reason=step.error_reason,
                        error_retryable=step.retryable,
                        error_retry_after_s=step.retry_after_s,
                    ),
                )
                return

        has_tool_calls = any(isinstance(step, ScriptedToolCall) for step in steps)
        yield EventDone(
            reason="toolUse" if has_tool_calls else "stop",
            message=message(stop_reason="toolUse" if has_tool_calls else "stop"),
        )
