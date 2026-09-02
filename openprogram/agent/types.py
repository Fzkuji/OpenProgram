"""
Agent types — mirrors packages/agent/src/types.ts
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Protocol, Union

from pydantic import BaseModel, Field, model_validator

from openprogram.providers.types import (
    AssistantMessageEvent,
    ImageContent,
    Message,
    Model,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolResultMessage,
)

# ThinkingLevel

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# StreamFn

from typing import AsyncGenerator, Protocol

class StreamFn(Protocol):
    """
    Protocol for stream functions that match stream_simple signature.
    Mirrors StreamFn type in TypeScript.
    """
    def __call__(
        self,
        model: Model,
        context: "AgentContext",
        options: SimpleStreamOptions | None = None
    ) -> AsyncGenerator[AssistantMessageEvent, None]:
        ...

# AgentMessage

# CustomAgentMessages is a protocol that can be extended via Union
# Applications can extend this by creating a Union with their custom message types
# For example: AgentMessage = Union[Message, BashExecutionMessage, CustomMessage]
CustomAgentMessages = Union[tuple]  # Empty union placeholder

# AgentMessage is the union of LLM messages plus any custom message types
# Custom message types can be added by extending this union in application code
AgentMessage = Union[Message, CustomAgentMessages]

# AgentLoopConfig


class AgentLoopConfig(SimpleStreamOptions):
    """
    Configuration for the agent loop — mirrors AgentLoopConfig in TypeScript.
    """
    model: Model

    # Caller-set cap on inner loop rounds (one round = one model call
    # plus its tool executions). ``None`` = only the framework's hard
    # MAX_INNER_ITERATIONS applies; an explicit value is clamped to it.
    # ``tool_choice`` / ``parallel_tool_calls`` ride in via the
    # SimpleStreamOptions base and are forwarded to the provider call.
    max_iterations: int | None = None

    # Converts AgentMessage[] to LLM-compatible Message[]
    convert_to_llm: Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]

    # Optional transform applied to context before convert_to_llm
    transform_context: Callable[[list[AgentMessage], asyncio.Event | None], Awaitable[list[AgentMessage]]] | None = None

    # Resolves API key dynamically per call
    get_api_key: Callable[[str], str | None | Awaitable[str | None]] | None = None

    # Returns steering messages to inject mid-run
    get_steering_messages: Callable[[], Awaitable[list[AgentMessage]]] | None = None

    # Returns follow-up messages after agent would stop
    get_follow_up_messages: Callable[[], Awaitable[list[AgentMessage]]] | None = None

    # Driver-owned durable boundary hook.  It receives only completed
    # provider/tool data or an explicit pre-dispatch intent; it never receives
    # a provider stream, task, stack frame, or other process-local object.
    safe_point_hook: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

    model_config = {"arbitrary_types_allowed": True}


# AgentTool

class AgentToolResult(BaseModel):
    """Result of a tool execution."""
    content: list[TextContent | ImageContent]
    details: Any = None
    is_error: bool = False

    @model_validator(mode="before")
    @classmethod
    def _bridge_legacy_details_error(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "is_error" in data:
            return data
        details = data.get("details")
        if isinstance(details, dict) and "is_error" in details:
            bridged = dict(data)
            bridged["is_error"] = bool(details["is_error"])
            return bridged
        return data


AgentToolUpdateCallback = Callable[["AgentToolResult"], None]


class AgentTool(Tool):
    """
    An agent tool with an execute function.
    Mirrors AgentTool<TParameters> interface in TypeScript.
    """
    label: str
    execute: Callable[
        [str, dict[str, Any], asyncio.Event | None, AgentToolUpdateCallback | None],
        Awaitable["AgentToolResult"],
    ]

    model_config = {"arbitrary_types_allowed": True}


# AgentContext

class AgentContext(BaseModel):
    """Context for agent operations."""
    system_prompt: str = ""
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] | None = None
    # Memory recalled for THIS turn, rendered as a prefix block inside the
    # wire user message rather than appended to the system prompt — which is
    # what keeps the system prompt byte-stable across turns (dag/overview.md
    # §7). Empty string = nothing recalled. The dispatcher supplies it (it
    # also stamps it on the user node); the loop falls back to recalling it
    # itself for entry points that don't.
    memory_prefetch: str | None = None

    model_config = {"arbitrary_types_allowed": True}


# AgentState

class AgentState(BaseModel):
    """Complete agent state."""
    system_prompt: str = ""
    # ``None`` only as a transient placeholder at construction — the real
    # model is applied immediately via ``initial_state`` (AgentSession always
    # supplies one). Optional so construction never depends on a particular
    # provider being enabled (post enabled-models migration).
    model: Model | None = None
    thinking_level: ThinkingLevel = "off"
    tools: list[AgentTool] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    is_streaming: bool = False
    stream_message: AgentMessage | None = None
    pending_tool_calls: set[str] = Field(default_factory=set)
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True}


# AgentEvent

class AgentEventAgentStart(BaseModel):
    type: Literal["agent_start"] = "agent_start"


class AgentEventAgentEnd(BaseModel):
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]

    model_config = {"arbitrary_types_allowed": True}


class AgentEventTurnStart(BaseModel):
    type: Literal["turn_start"] = "turn_start"


class AgentEventTurnEnd(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: list[ToolResultMessage]

    model_config = {"arbitrary_types_allowed": True}


class AgentEventMessageStart(BaseModel):
    type: Literal["message_start"] = "message_start"
    message: AgentMessage

    model_config = {"arbitrary_types_allowed": True}


class AgentEventMessageUpdate(BaseModel):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent

    model_config = {"arbitrary_types_allowed": True}


class AgentEventMessageEnd(BaseModel):
    type: Literal["message_end"] = "message_end"
    message: AgentMessage

    model_config = {"arbitrary_types_allowed": True}


class AgentEventToolStart(BaseModel):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str
    tool_name: str
    args: Any


class AgentEventToolUpdate(BaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class AgentEventToolEnd(BaseModel):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool


AgentEvent = Union[
    AgentEventAgentStart,
    AgentEventAgentEnd,
    AgentEventTurnStart,
    AgentEventTurnEnd,
    AgentEventMessageStart,
    AgentEventMessageUpdate,
    AgentEventMessageEnd,
    AgentEventToolStart,
    AgentEventToolUpdate,
    AgentEventToolEnd,
]
