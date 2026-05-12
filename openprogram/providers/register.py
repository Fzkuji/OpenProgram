"""
Register all built-in API providers.

Mirrors register-builtins.ts — registers Anthropic, OpenAI (Completions + Responses),
OpenAI Codex, Google (Generative AI + Vertex + Gemini CLI), and Amazon Bedrock.
"""

from __future__ import annotations

from openprogram.providers.api_registry import register_api_provider
# Provider submodules are imported lazily inside register_builtins()
# below. Importing them at module top would force re-entry into the
# parent providers package while it's still being initialized, which
# Python flags as "partially initialized module" when more than one
# thread is in the import chain at the same time (the worker's
# session-restore thread races the main webui startup).


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

    # Lazy imports so register.py module load doesn't pull these
    # submodules into the still-loading providers package.
    from openprogram.providers import anthropic, google, openai_completions

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
        from openprogram.providers.openai_responses import stream_simple_openai_responses
        from openprogram.providers.openai_responses import stream_openai_responses
        register_api_provider(
            "openai-responses",
            _StreamFnProvider(stream_openai_responses, stream_simple_openai_responses),
            source_id="builtin",
        )
    except ImportError:
        pass

    # OpenAI Codex Responses API
    try:
        from openprogram.providers.openai_codex.openai_codex import stream_simple_openai_codex_responses
        from openprogram.providers.openai_codex.openai_codex import stream_openai_codex_responses
        register_api_provider(
            "openai-codex",
            _StreamFnProvider(stream_openai_codex_responses, stream_simple_openai_codex_responses),
            source_id="builtin",
        )
        # Side-effect import: registers the OAuth refresh fn with
        # AuthManager so codex's stream funcs can acquire/refresh the
        # ChatGPT OAuth access_token. Without this import,
        # register_codex_auth() never runs and acquiring credentials
        # via AuthManager raises ProviderConfigMissing — manifesting
        # as "No API key for provider: openai-codex" the moment a
        # channel-routed turn fires.
        from openprogram.providers.openai_codex import auth_adapter as _codex_auth  # noqa: F401
    except ImportError:
        pass

    # Google Generative AI
    register_api_provider(
        "google-generative-ai",
        _StreamFnProvider(google.stream_simple, google.stream_simple),
        source_id="builtin",
    )

    # Google Gemini CLI / Cloud Code Assist
    # (google-vertex + gemini-subscription-corp/Antigravity intentionally
    # not registered — the simpler "Google AI" + "Gemini CLI" pair
    # covers the same model surface for personal users.)
    try:
        from openprogram.providers.google_gemini_cli import stream_google_gemini_cli
        from openprogram.providers.google_gemini_cli import stream_simple_google_gemini_cli
        register_api_provider(
            "gemini-subscription",
            _StreamFnProvider(stream_google_gemini_cli, stream_simple_google_gemini_cli),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Amazon Bedrock Converse Stream
    try:
        from openprogram.providers.amazon_bedrock import stream_bedrock
        from openprogram.providers.amazon_bedrock import stream_simple_bedrock
        register_api_provider(
            "bedrock-converse-stream",
            _StreamFnProvider(stream_bedrock, stream_simple_bedrock),
            source_id="builtin",
        )
    except ImportError:
        pass

    # Azure OpenAI Responses API
    try:
        from openprogram.providers.azure_openai_responses import stream_simple_azure_openai_responses
        from openprogram.providers.azure_openai_responses import stream_azure_openai_responses
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
