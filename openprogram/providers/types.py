"""
Core type definitions — mirrors packages/ai/src/types.ts
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Union

from pydantic import BaseModel, Field

# ─── Provider / API identifiers ──────────────────────────────────────────────

KnownApi = Literal[
    "openai-completions",
    "mistral-conversations",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "bedrock-converse-stream",
    "google-generative-ai",
    "google-gemini-cli",
    "google-vertex",
]
Api = str  # KnownApi or arbitrary string

KnownProvider = Literal[
    "amazon-bedrock",
    "anthropic",
    "google",
    "google-gemini-cli",
    "google-antigravity",
    "google-vertex",
    "openai",
    "azure-openai-responses",
    "openai-codex",
    "github-copilot",
    "xai",
    "groq",
    "cerebras",
    "openrouter",
    "vercel-ai-gateway",
    "zai",
    "mistral",
    "minimax",
    "minimax-cn",
    "huggingface",
    "opencode",
    "opencode-go",
    "kimi-coding",
]
Provider = str  # KnownProvider or arbitrary string

ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
CacheRetention = Literal["none", "short", "long"]
Transport = Literal["sse", "websocket", "auto"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]


# ─── Compat types ─────────────────────────────────────────────────────────────

@dataclass
class OpenAICompletionsCompat:
    """Compatibility settings for OpenAI Completions API."""
    supports_store: bool = False
    supports_developer_role: bool = False
    reasoning_effort_map: dict[str, str] | None = None
    supports_usage_in_streaming: bool = False


@dataclass
class OpenAIResponsesCompat:
    """Compatibility settings for OpenAI Responses API."""
    pass  # Currently empty, reserved for future extensions


@dataclass
class OpenRouterRouting:
    """Routing configuration for OpenRouter."""
    only: list[str] | None = None
    order: list[str] | None = None


@dataclass
class VercelGatewayRouting:
    """Routing configuration for Vercel Gateway."""
    only: list[str] | None = None
    order: list[str] | None = None


# ─── Thinking budgets ─────────────────────────────────────────────────────────

class ThinkingBudgets(BaseModel):
    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None


# ─── Stream options ───────────────────────────────────────────────────────────

class StreamOptions(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = None
    signal: Any | None = None
    api_key: str | None = None
    transport: Transport | None = None
    cache_retention: CacheRetention | None = "short"
    session_id: str | None = None
    on_payload: (
        Callable[[Any, "Model"], Any | None] | 
        Callable[[Any, "Model"], Awaitable[Any | None]] | 
        None
    ) = None
    headers: dict[str, str] | None = None
    max_retry_delay_ms: int | None = 60000
    metadata: dict[str, Any] | None = None

    model_config = {"arbitrary_types_allowed": True}

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style .get() for backwards compatibility with provider code.

        Provider implementations use opts.get("field") to safely read options
        without raising AttributeError. This mirrors TypeScript's optional
        chaining: ``options?.field``.
        """
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-style [] access for backwards compatibility.

        Used in provider code like: ``opts["on_payload"](params, model)``.
        """
        value = getattr(self, key, None)
        if value is None:
            raise KeyError(key)
        return value


class SimpleStreamOptions(StreamOptions):
    """Unified options with reasoning — passed to stream_simple() / complete_simple()."""
    reasoning: ThinkingLevel | None = None
    thinking_budgets: ThinkingBudgets | None = None


# ─── Content blocks ───────────────────────────────────────────────────────────

class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None


class TextSignatureV1(BaseModel):
    """Structured text signature for OpenAI Responses API (v1 format)."""
    v: Literal[1] = 1
    id: str
    phase: Literal["commentary", "final_answer"] | None = None


class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = None
    redacted: bool | None = None  # True for Anthropic redacted_thinking blocks


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str  # base64 encoded
    mime_type: str  # e.g. "image/jpeg"


class ToolCall(BaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    thought_signature: str | None = None  # Google-specific


# ─── Usage / cost ─────────────────────────────────────────────────────────────

class UsageCost(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: UsageCost = Field(default_factory=UsageCost)


# ─── Messages ─────────────────────────────────────────────────────────────────

class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent]
    timestamp: int  # Unix ms


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[TextContent | ThinkingContent | ToolCall]
    api: Api
    provider: Provider
    model: str
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = "stop"
    error_message: str | None = None
    timestamp: int  # Unix ms


class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    details: Any | None = None
    is_error: bool = False
    timestamp: int  # Unix ms


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


# ─── Tool ─────────────────────────────────────────────────────────────────────

class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


# ─── Context ──────────────────────────────────────────────────────────────────

class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message] = Field(default_factory=list)
    tools: list[Tool] | None = None


# ─── Model ────────────────────────────────────────────────────────────────────

class ModelCost(BaseModel):
    input: float = 0.0   # $/million tokens
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class Model(BaseModel):
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool = False
    input: list[Literal["text", "image"]] = Field(default_factory=lambda: ["text"])
    cost: ModelCost = Field(default_factory=ModelCost)
    context_window: int = 128000
    max_tokens: int = 8192
    headers: dict[str, str] | None = None
    compat: (
        OpenAICompletionsCompat | 
        OpenAIResponsesCompat | 
        OpenRouterRouting | 
        VercelGatewayRouting | 
        dict[str, Any] | 
        None
    ) = None


# ─── Streaming events ─────────────────────────────────────────────────────────

class EventStart(BaseModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class EventTextStart(BaseModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class EventTextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventTextEnd(BaseModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class EventThinkingStart(BaseModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class EventThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventThinkingEnd(BaseModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class EventToolCallStart(BaseModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class EventToolCallDelta(BaseModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class EventToolCallEnd(BaseModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


class EventDone(BaseModel):
    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class EventError(BaseModel):
    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    error: AssistantMessage


AssistantMessageEvent = Union[
    EventStart,
    EventTextStart,
    EventTextDelta,
    EventTextEnd,
    EventThinkingStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventToolCallStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventDone,
    EventError,
]

# Async generator of AssistantMessageEvent
AssistantMessageEventStream = AsyncGenerator[AssistantMessageEvent, None]

# StreamFunction type alias
StreamFunction = Callable[
    ["Model", "Context", "SimpleStreamOptions | None"],
    "AssistantMessageEventStream",
]
