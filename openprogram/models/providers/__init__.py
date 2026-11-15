"""
pi_ai — Unified LLM API
Python mirror of @mariozechner/pi-ai.

Provider detection is :func:`detect_provider`. Building a Runtime is the
runtime layer's (``openprogram.runtime.agent.runtime.factory.create_runtime``,
with the provider-specific classes in
``openprogram.runtime.agent.provider_runtimes``); the provider packages
here hold only model, auth and wire code.
"""

# Core types
from .types import (
    Api,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantMessageEventStream,
    CacheRetention,
    Context,
    EventDone,
    EventError,
    EventStart,
    EventStructuredOutputEnd,
    EventStructuredOutputRetry,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventThinkingStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventToolCallStart,
    EventServerToolStart,
    EventServerToolEnd,
    Citation,
    ServerToolUse,
    ImageContent,
    KnownApi,
    KnownProvider,
    Message,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    Provider,
    SimpleStreamOptions,
    StopReason,
    StreamOptions,
    TextContent,
    ThinkingBudgets,
    ThinkingContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolResultMessage,
    Transport,
    Usage,
    UsageCost,
    UserMessage,
    VercelGatewayRouting,
)
from .structured_output import (
    JsonSchemaOutput,
    StructuredOutputCapabilities,
    StructuredOutputPlan,
)

# The model registry below reads the models.dev catalogue and may want to
# refresh it. No refresh thread may start while this package is still
# importing (it would deadlock with this import; see
# models_dev.hold_background_refresh), so the hold is taken here and released
# as the last statement of this module.
from .catalog.sources import models_dev as _models_dev

_models_dev.hold_background_refresh()

# Model registry
from .catalog.models import calculate_cost, get_model, get_models, get_providers, models_are_equal, supports_xhigh

# API registry
from .catalog.api_registry import (
    ApiProviderSnapshot,
    configure_provider_transform,
    get_api_provider,
    get_api_provider_snapshot,
    get_structured_output_capabilities,
    resolve_api_provider_snapshot,
    resolve_structured_output_capabilities,
    register_api_provider,
)

# Environment API keys
from .settings.env_api_keys import resolve_provider_key

# Streaming functions
from .streaming.stream import complete, complete_simple, stream, stream_simple

# Utilities
from .utils.event_stream import AssistantMessageEventStream as AssistantMessageEventStreamClass, EventStream, create_assistant_message_event_stream
from .utils.json_parse import parse_partial_json, parse_streaming_json
from .utils.overflow import is_context_overflow, get_overflow_patterns
from .utils.validation import validate_tool_arguments, validate_tool_call
from .utils.sanitize_unicode import sanitize_surrogates

# Provider detection (the Runtime factory is the runtime layer's).
from openprogram.models.providers.catalog.registry import PROVIDERS, check_providers, detect_provider

__all__ = [
    # Types
    "Api", "KnownApi", "KnownProvider", "Provider",
    "ThinkingLevel", "ThinkingBudgets", "CacheRetention", "Transport", "StopReason",
    "StreamOptions", "SimpleStreamOptions", "JsonSchemaOutput",
    "StructuredOutputCapabilities", "StructuredOutputPlan", "ApiProviderSnapshot",
    "TextContent", "ThinkingContent", "ImageContent", "ToolCall",
    "Citation", "ServerToolUse",
    "Usage", "UsageCost",
    "UserMessage", "AssistantMessage", "ToolResultMessage", "Message",
    "Tool", "Context", "Model", "ModelCost",
    "OpenAICompletionsCompat", "OpenAIResponsesCompat", "OpenRouterRouting", "VercelGatewayRouting",
    "AssistantMessageEvent", "AssistantMessageEventStream",
    "EventStart", "EventTextStart", "EventTextDelta", "EventTextEnd",
    "EventStructuredOutputRetry", "EventStructuredOutputEnd",
    "EventThinkingStart", "EventThinkingDelta", "EventThinkingEnd",
    "EventToolCallStart", "EventToolCallDelta", "EventToolCallEnd",
    "EventServerToolStart", "EventServerToolEnd",
    "EventDone", "EventError",
    # Models
    "get_model", "get_providers", "get_models", "calculate_cost", "supports_xhigh", "models_are_equal",
    # Registry
    "register_api_provider", "get_api_provider", "get_api_provider_snapshot",
    "get_structured_output_capabilities", "resolve_api_provider_snapshot",
    "resolve_structured_output_capabilities", "configure_provider_transform",
    # Detection
    "PROVIDERS", "detect_provider", "check_providers",
    # Keys
    "resolve_provider_key",
    # Streaming
    "stream", "complete", "stream_simple", "complete_simple",
    # Utils
    "EventStream", "AssistantMessageEventStreamClass", "create_assistant_message_event_stream",
    "parse_partial_json", "parse_streaming_json",
    "validate_tool_arguments", "validate_tool_call",
    "is_context_overflow", "get_overflow_patterns",
    "sanitize_surrogates",
]

_models_dev.release_background_refresh()
