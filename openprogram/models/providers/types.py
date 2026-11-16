"""
Core type definitions — mirrors packages/ai/src/types.ts
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from .structured_output import JsonSchemaOutput

# Provider / API identifiers

KnownApi = Literal[
    "openai-completions",
    "mistral-conversations",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex",
    "anthropic-messages",
    "bedrock-converse-stream",
    "google-generative-ai",
    "gemini-subscription",
]
Api = str  # KnownApi or arbitrary string

KnownProvider = Literal[
    "amazon-bedrock",
    "anthropic",
    "google",
    "gemini-subscription",
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

ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh", "max"]
CacheRetention = Literal["none", "short", "long"]
Transport = Literal["sse", "websocket", "auto"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]


# Compat types

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


# Thinking budgets

class ThinkingBudgets(BaseModel):
    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None


# Stream options

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
    # Per-request speed / priority tier. Maps to the provider's own
    # service-tier knob: OpenAI ``service_tier`` ("priority" = the
    # "Fast" mode, "flex" = cheaper-slower); Anthropic ``service_tier``
    # ("priority"/"standard_only"). ``None`` = the provider default.
    # Read by the request builders via ``opts.get("service_tier")``.
    service_tier: str | None = None
    # Tool-pick policy for this request: "auto" (provider default) /
    # "required" (must pick a tool) / "none" (text only) /
    # {"type": "function", "name": "X"} (force that tool). Each request
    # builder maps it to its provider's own shape; ``None`` = omit.
    tool_choice: Any | None = None
    # False forbids several tool calls in one round where the provider
    # supports the knob. ``None`` = provider default.
    parallel_tool_calls: bool | None = None
    # Enable the provider's built-in server-side web search for this request.
    # Only meaningful when the resolved API provider advertises
    # ``supports_web_search``; each request builder adds its own tool
    # (Responses/codex ``{"type": "web_search"}``, Anthropic
    # ``web_search_20250305``, Gemini ``google_search``). The searches come
    # back as ``ServerToolUse`` blocks. ``None`` / False = no web search.
    # Read via ``opts.get("web_search")``.
    web_search: bool | None = None
    response_format: JsonSchemaOutput | None = None
    # Request-scoped effect retry contract. A key is meaningful only when the
    # resolved provider explicitly advertises support for it.
    supports_idempotency_key: bool = False
    idempotency_key: str | None = None
    # The credential this request is sent with (``models/auth/selection.py``
    # ``Lease``: id, label, kind, auth value, base URL, headers), set by the
    # credential layer every adapter is wrapped in; ``api_key`` holds the
    # same auth value. ``None`` for a request that carries a caller's own key.
    credential: Any | None = Field(default=None, exclude=True)

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
    # "off" reaches an adapter only when its provider has a wire value for
    # it (``thinking_spec.off_wire_value``); it is never a picker level.
    reasoning: ThinkingLevel | Literal["off"] | None = None
    thinking_budgets: ThinkingBudgets | None = None


# Content blocks

class Citation(BaseModel):
    """A source the provider attributes part of a text block to.

    ``start`` / ``end`` locate the cited span in the text when the provider
    reports it (Responses ``url_citation`` indices, Gemini segment offsets);
    ``None`` when it does not (Anthropic cites by quoted text).
    """
    url: str
    title: str | None = None
    start: int | None = None
    end: int | None = None


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None
    # Sources the provider cited for this text (provider-side web search).
    # Display only: request converters never send them back.
    citations: list[Citation] | None = None
    # Optional caller-supplied prompt-cache breakpoint, passed through to
    # providers that support explicit caching (Anthropic). ``None`` means the
    # provider's default behaviour is unchanged.
    cache_control: dict | None = None


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
    # Optional caller-supplied prompt-cache breakpoint, passed through to
    # providers that support explicit caching (Anthropic). ``None`` means the
    # provider's default behaviour is unchanged.
    cache_control: dict | None = None


class VideoContent(BaseModel):
    type: Literal["video"] = "video"
    data: str  # base64 encoded
    mime_type: str  # e.g. "video/mp4"


class AudioContent(BaseModel):
    type: Literal["audio"] = "audio"
    data: str  # base64 encoded
    mime_type: str  # e.g. "audio/mp3"


class ToolCall(BaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    thought_signature: str | None = None  # Google-specific


class ServerToolUse(BaseModel):
    """A tool the provider ran on its own side (today: web search).

    The agent loop never executes it and it never pairs with a
    ``ToolResultMessage``; ``results`` already holds what the provider
    reported. ``raw`` keeps the provider's own item(s) so the block can be
    replayed to the same API in its native shape.
    """
    type: Literal["serverToolUse"] = "serverToolUse"
    id: str                 # provider item id
    name: str               # "web_search"
    input: dict[str, Any] = Field(default_factory=dict)  # {"query": str}
    status: str = "completed"  # "searching" | "completed" | "failed"
    results: list[dict[str, Any]] = Field(default_factory=list)  # [{"title", "url"}]
    raw: Any | None = None  # provider item, for replay


# Usage / cost

class UsageCost(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(BaseModel):
    # Preserve missing-vs-explicit-zero through serialized provider messages.
    tokens_reported: bool = False

    @model_validator(mode="before")
    @classmethod
    def _reported_counters(cls, value):
        if isinstance(value, dict) and "tokens_reported" not in value:
            return {**value, "tokens_reported": {"input", "output"}.issubset(value)}
        return value

    requested_service_tier: str | None = None
    service_tier: str | None = None
    provider_cost_usd: float | None = None
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    # Local estimate of the exact request that produced this usage. It is
    # paired with provider input usage to calibrate later graph estimates.
    request_input_estimate: int = 0
    # Time to first token, measured where every provider stream passes
    # (streaming/first_token.py): from sending the request to the reply's
    # first content of any kind, and to its first visible text. ``None``
    # when the reply had none of that kind or was not streamed here.
    first_token_ms: int | None = None
    first_text_ms: int | None = None
    cost: UsageCost = Field(default_factory=UsageCost)


# Messages

class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent | VideoContent | AudioContent]
    # On a compaction summary rendered as user input: the deferred tools it
    # carries (see ``AssistantMessage.carried_tools``). Never stored.
    carried_tools: list[dict[str, Any]] | None = Field(default=None, exclude=True)
    timestamp: int  # Unix ms


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[TextContent | ThinkingContent | ToolCall | ServerToolUse]
    api: Api
    provider: Provider
    model: str
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = "stop"
    error_message: str | None = None
    # Structured error taxonomy (populated when stop_reason == "error"), so
    # surfaces above the provider layer can tell a retryable rate-limit from a
    # fatal auth/context failure instead of just a string. See
    # docs/design/providers/reliability/error-taxonomy-propagation.md.
    error_reason: str | None = None         # ErrorReason value, e.g. "rate_limit"
    error_retryable: bool | None = None
    # A generic exception's inferred verdict is not a provider retry policy.
    # Existing producers/wire messages retain authoritative verdicts by default.
    error_retryable_inferred: bool = False
    error_retry_after_s: float | None = None
    # Which limit or refusal stopped the reply (``errors.failure_cause``):
    # ``{"cause", "http_status", "provider", "limit", "resets_at", "account",
    # "other_limits"}`` with
    # cause ``quota_exhausted`` / ``invalid_key`` / ``not_available`` /
    # ``unreachable``; ``None`` for any other failure.
    error_cause: dict[str, Any] | None = None
    # The provider already consumed its complete transport retry budget.
    # Higher retry layers must not start another identical budget.
    error_transport_exhausted: bool | None = None
    structured_output: Any | None = None
    structured_output_mode: Literal["native", "tool", "prompt"] | None = None
    structured_output_attempt: int | None = None
    # Models that failed before this reply streamed anything, in the order
    # they were tried: ``{"provider", "model", "reason"}`` with ``reason`` a
    # FailoverCategory value. ``provider``/``model`` above name the model
    # that answered. Empty when the first model answered.
    failover: list[dict[str, str]] = Field(default_factory=list)
    # The stored credential that sent this reply (``models/auth``), for the
    # usage log; ``None`` for a caller's own key or a provider with none.
    credential_id: str | None = None
    # On a compaction summary only: the deferred tools it carries past the
    # turns it covers, as ``{"name", "definition"}`` records
    # (``shared.tool_loads.carried_records``). Rendered from the summary
    # node on every request, never stored with a message.
    carried_tools: list[dict[str, Any]] | None = Field(default=None, exclude=True)
    timestamp: int  # Unix ms

    @classmethod
    def empty(cls, model: Any, *, api: str | None = None) -> "AssistantMessage":
        """A reply with no content yet, stamped now, for a stream to fill."""
        import time
        return cls(content=[], api=api or model.api, provider=model.provider, model=model.id,
                   usage=Usage(), stop_reason="stop", timestamp=int(time.time() * 1000))


class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent | VideoContent | AudioContent]
    details: Any | None = None
    is_error: bool = False
    timestamp: int  # Unix ms


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


# Tool

class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object
    # Optional prompt-cache breakpoint, passed through to providers that
    # support explicit tool-level cache markers (Anthropic / Bedrock). Set by
    # the cache-policy pass (cache_policy.py) or by the caller. None = no marker.
    cache_control: dict | None = None
    # Sent with ``defer_loading: true`` (the Anthropic ``tool_reference``
    # form, settings/tool_search_spec.py): outside the cached prefix and the
    # tool-set check until a ``tool_reference`` names it.
    defer_loading: bool = False


# AgentTool — a Tool with the function that runs it, and what running it returns.

class AgentToolResult(BaseModel):
    """Result of a tool execution."""
    content: list[TextContent | ImageContent | AudioContent]
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


# Context

class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message] = Field(default_factory=list)
    tools: list[Tool] | None = None


# Model

class ModelCost(BaseModel):
    # ``None`` means the price is absent, which is distinct from an explicit
    # zero rate (for example a bundled or free endpoint).  Do not turn absent
    # catalog fields into zero: budget enforcement needs this distinction.
    input: float | None = None   # $/million tokens
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    source: Literal["model_catalog", "configured", "unknown"] = "unknown"

    @field_validator("input", "output", "cache_read", "cache_write")
    @classmethod
    def _valid_rate(cls, value: float | None) -> float | None:
        if value is not None and (value < 0 or value == float("inf") or value != value):
            raise ValueError("price rates must be finite and non-negative")
        return value

    def is_known(self) -> bool:
        return (
            (self.source != "unknown" or "source" not in self.model_fields_set)
            and self.input is not None
            and self.output is not None
            and self.cache_read is not None
            and self.cache_write is not None
        )


class Model(BaseModel):
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool = False
    structured_output: bool | None = None
    # Declared thinking capability. Empty list = model doesn't support
    # reasoning; UI hides the menu entirely. `reasoning: bool` stays as the
    # simple "anything at all?" flag (kept for backward compat with
    # `supports_xhigh` and existing catalog code).
    thinking_levels: list[ThinkingLevel] = Field(default_factory=list)
    default_thinking_level: ThinkingLevel | None = None
    # Optional tag for models whose thinking UX / request body diverges from
    # the default path (LobeChat-style quirk marker). Examples: "opus47",
    # "codex_max". Provider request builders and the UI picker switch on this.
    thinking_variant: str | None = None
    # 高速（Fast）档声明：支持的模型写 True，不支持的不写（默认 False）。
    # GPT 系在线上叫 service_tier="priority"；Claude Opus 4.6+ 走
    # speed:"fast" + fast-mode beta 头。声明表见 enabled_models.default_fast。
    fast: bool = False
    # Native tool search: False opts this model out of the form its provider
    # declares for the API (settings/tool_search_spec.py); None follows it.
    tool_search: bool | None = None
    input: list[Literal["text", "image", "video", "audio"]] = Field(default_factory=lambda: ["text"])
    cost: ModelCost = Field(default_factory=ModelCost)
    context_window: int = 128000
    max_tokens: int = 8192
    # How much one stored tool result may weigh (``state.store.session.weight``: a CJK
    # character counts 1, anything else 0.25), as Codex's per-model
    # ``truncation_policy``. None means the default
    # (``spill.DEFAULT_TOOL_OUTPUT_WEIGHT``); either way at most 30% of the
    # window (``spill.result_cap``).
    tool_output_weight: int | None = None
    headers: dict[str, str] | None = None
    compat: (
        OpenAICompletionsCompat | 
        OpenAIResponsesCompat | 
        OpenRouterRouting | 
        VercelGatewayRouting | 
        dict[str, Any] | 
        None
    ) = None


# Streaming events

class EventStart(BaseModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class EventTextStart(BaseModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage
    output_attempt: int | None = None


class EventTextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage
    output_attempt: int | None = None


class EventTextEnd(BaseModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage
    output_attempt: int | None = None


class EventStructuredOutputRetry(BaseModel):
    type: Literal["structured_output_retry"] = "structured_output_retry"
    attempt: int
    next_attempt: int
    issues: list[dict[str, str]] = Field(default_factory=list)


class EventRetry(BaseModel):
    type: Literal["retry"] = "retry"
    attempt: int
    max_attempts: int | None = None
    reason: str
    delay_ms: int = 0
    # What failed, in one short line, for the retry row's detail.
    error: str | None = None
    # True when the request is sent again from its start after content had
    # streamed: what this response showed so far is void, and its text and
    # reasoning are no longer shown. Absent otherwise.
    restart: bool | None = None
    # A provider's credential changed under the request (reason
    # ``credential``: ``{"to", "from", "cause"}``, the labels and why the
    # first stopped: rate_limit / quota / auth / model) or the request
    # waits for one (``credential_wait``: ``{"to"}``). Absent otherwise.
    credential: dict | None = None


class EventStructuredOutputEnd(BaseModel):
    type: Literal["structured_output_end"] = "structured_output_end"
    attempt: int
    mode: Literal["native", "tool", "prompt"]
    value: Any


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


class EventServerToolStart(BaseModel):
    type: Literal["servertool_start"] = "servertool_start"
    content_index: int
    partial: AssistantMessage


class EventServerToolEnd(BaseModel):
    type: Literal["servertool_end"] = "servertool_end"
    content_index: int
    server_tool: ServerToolUse
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
    EventStructuredOutputRetry,
    EventRetry,
    EventStructuredOutputEnd,
    EventThinkingStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventToolCallStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventServerToolStart,
    EventServerToolEnd,
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
