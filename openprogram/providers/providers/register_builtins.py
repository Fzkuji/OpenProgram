"""
Register all built-in API providers.

Mirrors register-builtins.ts — registers Anthropic, OpenAI (Completions + Responses),
OpenAI Codex, Google (Generative AI + Vertex + Gemini CLI), and Amazon Bedrock.
"""

from __future__ import annotations

from openprogram.providers.api_registry import register_api_provider
from openprogram.providers.providers import anthropic, google, openai_completions


class _StreamFnProvider:
    """Adapts module-level stream/stream_simple functions to provider interface."""

    def __init__(self, stream_fn, stream_simple_fn):
        self._stream = stream_fn
        self._stream_simple = stream_simple_fn

    def stream(self, model, context, options=None):
        return self._stream(model, context, options)

    def stream_simple(self, model, context, options=None):
        return self._stream_simple(model, context, options)


_registered = False


def register_builtins() -> None:
    """Register all built-in API providers. Safe to call multiple times."""
    global _registered
    if _registered:
        return
    _registered = True

    # Anthropic Messages API
    register_api_provider(
        "anthropic-messages",
        _StreamFnProvider(anthropic.stream_simple, anthropic.stream_simple),
        source_id="builtin",
    )

    # OpenAI Chat Completions API
    register_api_provider(
        "openai-completions",
        _StreamFnProvider(openai_completions.stream_simple, openai_completions.stream_simple),
        source_id="builtin",
    )

    # OpenAI Responses API
    try:
        from openprogram.providers.providers.openai_responses import stream_simple_openai_responses
        from openprogram.providers.providers.openai_responses import stream_openai_responses
        register_api_provider(
            "openai-responses",
            _StreamFnProvider(stream_openai_responses, stream_simple_openai_responses),
            source_id="builtin",
        )
    except ImportError:
        pass

    # OpenAI Codex Responses API
    try:
        from openprogram.providers.providers.openai_codex_responses import stream_simple_openai_codex_responses
        from openprogram.providers.providers.openai_codex_responses import stream_openai_codex_responses
        register_api_provider(
            "openai-codex-responses",
            _StreamFnProvider(stream_openai_codex_responses, stream_simple_openai_codex_responses),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Google Generative AI
    register_api_provider(
        "google-generative-ai",
        _StreamFnProvider(google.stream_simple, google.stream_simple),
        source_id="builtin",
    )

    # Google Vertex AI
    try:
        from openprogram.providers.providers.google_vertex import stream_google_vertex
        from openprogram.providers.providers.google_vertex import stream_simple_google_vertex
        register_api_provider(
            "google-vertex",
            _StreamFnProvider(stream_google_vertex, stream_simple_google_vertex),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Google Gemini CLI / Cloud Code Assist
    try:
        from openprogram.providers.providers.google_gemini_cli import stream_google_gemini_cli
        from openprogram.providers.providers.google_gemini_cli import stream_simple_google_gemini_cli
        register_api_provider(
            "google-gemini-cli",
            _StreamFnProvider(stream_google_gemini_cli, stream_simple_google_gemini_cli),
            source_id="builtin",
        )
        register_api_provider(
            "google-antigravity",
            _StreamFnProvider(stream_google_gemini_cli, stream_simple_google_gemini_cli),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Amazon Bedrock Converse Stream
    try:
        from openprogram.providers.providers.amazon_bedrock import stream_bedrock
        from openprogram.providers.providers.amazon_bedrock import stream_simple_bedrock
        register_api_provider(
            "bedrock-converse-stream",
            _StreamFnProvider(stream_bedrock, stream_simple_bedrock),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Azure OpenAI Responses API
    try:
        from openprogram.providers.providers.azure_openai_responses import stream_simple_azure_openai_responses
        from openprogram.providers.providers.azure_openai_responses import stream_azure_openai_responses
        register_api_provider(
            "azure-openai-responses",
            _StreamFnProvider(stream_azure_openai_responses, stream_simple_azure_openai_responses),
            source_id="builtin",
        )
    except ImportError:
        pass


def reset_api_providers() -> None:
    """Reset all registered providers (for testing purposes)."""
    global _registered
    from openprogram.providers.api_registry import _registry
    _registry.clear()
    _registered = False
