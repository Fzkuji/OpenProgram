"""
Agent types — mirrors packages/agent/src/types.ts
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Protocol, Union

from pydantic import PrivateAttr, BaseModel, Field

# The executable tool contract (``AgentTool``, ``AgentToolResult``) lives
# beside ``Tool`` in the provider types, so the permission layer can wrap
# tools without importing the call core.
from openprogram.models.providers import types as provider_types
from openprogram.models.providers.types import (
    AssistantMessageEvent,
    Message,
    Model,
    SimpleStreamOptions,
    ToolResultMessage,
)

# ThinkingLevel

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# StreamFn


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
    # plus its tool executions). ``None`` = unbounded (chat turns).
    # ``runtime.exec`` defaults to 20.
    # ``tool_choice`` / ``parallel_tool_calls`` ride in via the
    # SimpleStreamOptions base and are forwarded to the provider call.
    max_iterations: int | None = None
    # Caller-owned workflows consume receipts before requesting the next step.
    stop_after_tool_round: bool = False
    # Whether a request with no recalled memory given (``AgentContext
    # .memory_prefetch`` None) recalls some: a workflow role's request holds
    # only what the workflow put there.
    recall: bool = True
    # Whether a request with no date given (``AgentContext.current_date``
    # None) is told today's date beside its message: a model call built on
    # a prompt of its own (``runtime.exec``). A chat turn names the date
    # stored on its message.
    dated: bool = False
    # Somebody is watching this turn: after a long stretch of tool rounds with
    # nothing shown, the loop asks for a word of progress (loop/progress_note.py).
    progress_notes: bool = False

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

    # A conversation turn's durable compaction between two of its requests
    # (``turn_runtime/turn_compaction.py``). Called with the loop's messages
    # and why: ``"mid_turn"`` after a step's tool results when the next
    # request would pass the automatic threshold, ``"reactive"`` after the
    # provider reported an overflow. Returns the messages to continue with
    # (the compacted history, then this turn's own messages), or None when
    # nothing was compacted. Calls without a conversation leave it None.
    compact_turn: Callable[[list[AgentMessage], str], Awaitable[list[AgentMessage] | None]] | None = None

    model_config = {"arbitrary_types_allowed": True}



# AgentContext

class AgentContext(BaseModel):
    """Context for agent operations."""
    system_prompt: str = ""
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[provider_types.AgentTool] | None = None
    # Memory recalled for THIS turn, rendered as a prefix block inside the
    # wire user message rather than appended to the system prompt — which is
    # what keeps the system prompt byte-stable across turns (dag/overview.md
    # §7). Empty string = nothing recalled. The dispatcher supplies it (it
    # also stamps it on the user node); the loop falls back to recalling it
    # itself for entry points that don't.
    _request_compactor: Any = PrivateAttr(default=None)
    # The text of the user message this turn's prefix block went into, and the
    # block recalled for it, so later rounds put the same block in the same place.
    _prefetch_anchor: Any = PrivateAttr(default=None)
    memory_prefetch: str | None = None
    # Told to the model beside this turn's message: a scheduled run's last
    # result and how to answer. Not memory, so memory settings do not touch it.
    run_note: str | None = None
    # The date line sent beside this turn's message (``current_date_note``).
    # None: the loop writes today's date when the turn's first request is
    # made, if the loop is configured to (``AgentLoopConfig.dated``); ""
    # sends none (the message already carries the one it was sent with).
    current_date: str | None = None
    # The notes sent beside this turn's message between its date and its
    # recalled memory: a rewind note, the sections whose text changed since
    # the kept system prompt, the plan-mode and Goal notes
    # (``context.turn_context.notes_text``). None/"": none.
    turn_notes: str | None = None
    # Resolved immutable runtime contract used by durable safe points.  This
    # is metadata only; executable tool callbacks never enter the context.
    runtime_contract: dict[str, Any] | None = None

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
    tools: list[provider_types.AgentTool] = Field(default_factory=list)
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
    # Qualified occurrence identity. Providers may reuse a raw call id across
    # turns; stream/DAG consumers use this value for idempotency and lookup.
    occurrence_id: str | None = None
    # DAG visibility policy of the decorated tool. Hidden tools still emit
    # lifecycle events, but their arguments/results are never persisted.
    expose: str | None = None
    # Only an actual concurrent dispatcher supplies this id. The sequential
    # Python tool loop leaves it empty; consumers must not infer groups from
    # adjacency.
    group_id: str | None = None


class AgentEventToolUpdate(BaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any


class AgentEventToolEnd(BaseModel):
    outcome: Literal["not_started", "failed", "cancelled", "completed"] | None = None
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool
    occurrence_id: str | None = None
    expose: str | None = None
    group_id: str | None = None


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
