"""
pi_ai — Unified LLM API
Python mirror of @mariozechner/pi-ai.

This module also keeps the older ``openprogram.providers`` compatibility
surface alive, so existing imports like ``detect_provider`` or
``AnthropicRuntime`` continue to work during the provider refactor.
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
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingEnd,
    EventThinkingStart,
    EventToolCallDelta,
    EventToolCallEnd,
    EventToolCallStart,
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

# Model registry
from .models import calculate_cost, get_model, get_models, get_providers, models_are_equal, supports_xhigh

# API registry
from .api_registry import get_api_provider, get_api_providers, register_api_provider, unregister_api_providers, clear_api_providers

# Environment API keys
from .env_api_keys import get_env_api_key

# Streaming functions
from .stream import complete, complete_simple, stream, stream_simple

# Utilities
from .utils.event_stream import AssistantMessageEventStream as AssistantMessageEventStreamClass, EventStream, create_assistant_message_event_stream
from .utils.json_parse import parse_partial_json, parse_streaming_json
from .utils.overflow import is_context_overflow, get_overflow_patterns
from .utils.validation import validate_tool_arguments, validate_tool_call
from .utils.sanitize_unicode import sanitize_surrogates

# Backward-compatible provider detection/runtime helpers.
from openprogram.legacy_providers import PROVIDERS, check_providers, create_runtime, detect_provider

__all__ = [
    # Types
    "Api", "KnownApi", "KnownProvider", "Provider",
    "ThinkingLevel", "ThinkingBudgets", "CacheRetention", "Transport", "StopReason",
    "StreamOptions", "SimpleStreamOptions",
    "TextContent", "ThinkingContent", "ImageContent", "ToolCall",
    "Usage", "UsageCost",
    "UserMessage", "AssistantMessage", "ToolResultMessage", "Message",
    "Tool", "Context", "Model", "ModelCost",
    "OpenAICompletionsCompat", "OpenAIResponsesCompat", "OpenRouterRouting", "VercelGatewayRouting",
    "AssistantMessageEvent", "AssistantMessageEventStream",
    "EventStart", "EventTextStart", "EventTextDelta", "EventTextEnd",
    "EventThinkingStart", "EventThinkingDelta", "EventThinkingEnd",
    "EventToolCallStart", "EventToolCallDelta", "EventToolCallEnd",
    "EventDone", "EventError",
    # Models
    "get_model", "get_providers", "get_models", "calculate_cost", "supports_xhigh", "models_are_equal",
    # Registry
    "register_api_provider", "get_api_provider", "get_api_providers", "unregister_api_providers", "clear_api_providers",
    # Legacy runtime compatibility
    "PROVIDERS", "detect_provider", "create_runtime", "check_providers",
    "AnthropicRuntime", "OpenAIRuntime", "GeminiRuntime", "ClaudeCodeRuntime", "OpenAICodexRuntime", "GeminiCLIRuntime",
    # Keys
    "get_env_api_key",
    # Streaming
    "stream", "complete", "stream_simple", "complete_simple",
    # Utils
    "EventStream", "AssistantMessageEventStreamClass", "create_assistant_message_event_stream",
    "parse_partial_json", "parse_streaming_json",
    "validate_tool_arguments", "validate_tool_call",
    "is_context_overflow", "get_overflow_patterns",
    "sanitize_surrogates",
]


_DEFERRABLE_LEGACY_EXPORTS = {
    "AnthropicRuntime",
    "OpenAIRuntime",
    "GeminiRuntime",
    "ClaudeCodeRuntime",
    "OpenAICodexRuntime",
    "GeminiCLIRuntime",
}


def __getattr__(name: str):
    """Lazy-load legacy runtime classes while preserving modern exports."""
    if name in _DEFERRABLE_LEGACY_EXPORTS:
        from openprogram import legacy_providers
        return getattr(legacy_providers, name)
    raise AttributeError(f"module 'openprogram.providers' has no attribute {name!r}")


# Register builtin API providers AFTER the full package init completes.
# Doing this from inside stream.py at module load triggers a circular
# re-entry into providers/* when threads import in parallel.
from .stream import _ensure_builtins as _stream_ensure_builtins
_stream_ensure_builtins()
del _stream_ensure_builtins
