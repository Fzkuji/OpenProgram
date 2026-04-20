"""
Agent types — mirrors packages/agent/src/types.ts
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Protocol, Union

from pydantic import BaseModel, Field

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

# ─── ThinkingLevel ────────────────────────────────────────────────────────────

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# ─── StreamFn ─────────────────────────────────────────────────────────────────

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

# ─── AgentMessage ─────────────────────────────────────────────────────────────

# CustomAgentMessages is a protocol that can be extended via Union
# Applications can extend this by creating a Union with their custom message types
# For example: AgentMessage = Union[Message, BashExecutionMessage, CustomMessage]
CustomAgentMessages = Union[tuple]  # Empty union placeholder

# AgentMessage is the union of LLM messages plus any custom message types
# Custom message types can be added by extending this union in application code
AgentMessage = Union[Message, CustomAgentMessages]

# ─── AgentLoopConfig ──────────────────────────────────────────────────────────


class AgentLoopConfig(SimpleStreamOptions):
    """
    Configuration for the agent loop — mirrors AgentLoopConfig in TypeScript.
    """
    model: Model

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

    model_config = {"arbitrary_types_allowed": True}


# ─── AgentTool ────────────────────────────────────────────────────────────────

class AgentToolResult(BaseModel):
    """Result of a tool execution."""
    content: list[TextContent | ImageContent]
    details: Any = None


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


# ─── AgentContext ─────────────────────────────────────────────────────────────

class AgentContext(BaseModel):
    """Context for agent operations."""
    system_prompt: str = ""
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] | None = None

    model_config = {"arbitrary_types_allowed": True}


# ─── AgentState ───────────────────────────────────────────────────────────────

class AgentState(BaseModel):
    """Complete agent state."""
    system_prompt: str = ""
    model: Model
    thinking_level: ThinkingLevel = "off"
    tools: list[AgentTool] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    is_streaming: bool = False
    stream_message: AgentMessage | None = None
    pending_tool_calls: set[str] = Field(default_factory=set)
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True}


# ─── AgentEvent ───────────────────────────────────────────────────────────────

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
