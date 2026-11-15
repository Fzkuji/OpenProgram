"""Register the built-in API providers and authentication adapters."""

from __future__ import annotations

import threading

from openprogram.models.providers.catalog.api_registry import (
    ApiProviderSnapshot,
    _register_builtin_api_providers,
)
from openprogram.models.providers.structured_output import StructuredOutputCapabilities


class _StreamFnProvider:
    """Adapt module-level stream functions to the provider interface."""

    def __init__(
        self, stream_fn, stream_simple_fn, *,
        supports_idempotency_key=False, supports_web_search=False,
        bounds_own_stream_idle=False,
    ):
        self._stream = stream_fn
        self._stream_simple = stream_simple_fn
        self.supports_idempotency_key = supports_idempotency_key
        # The request builder adds the provider's own server-side web search
        # tool when ``web_search=True`` (docs/reference/design/providers/web-search.html).
        self.supports_web_search = supports_web_search
        # The adapter reads every attempt under its own idle bound, inside its
        # retry loop, so the stream chokepoint does not bound it again
        # (utils/stream_idle.py).
        self.bounds_own_stream_idle = bounds_own_stream_idle

    def stream(self, model, context, options=None):
        return self._stream(model, context, options)

    def stream_simple(self, model, context, options=None):
        return self._stream_simple(model, context, options)


_lock = threading.RLock()
_registered = False
_auth_registered = False


def _load_builtin_providers() -> dict[str, _StreamFnProvider]:
    from openprogram.models.providers.adapters import anthropic, google, openai_completions
    from openprogram.models.providers.adapters.amazon_bedrock import (
        stream_bedrock,
        stream_simple_bedrock,
    )
    from openprogram.models.providers.adapters.azure_openai_responses import (
        stream_azure_openai_responses,
        stream_simple_azure_openai_responses,
    )
    from openprogram.models.providers.adapters.google_gemini_cli import (
        stream_google_gemini_cli,
        stream_simple_google_gemini_cli,
    )
    from openprogram.models.providers.adapters.openai_codex.openai_codex import (
        stream_openai_codex_responses,
        stream_simple_openai_codex_responses,
    )
    from openprogram.models.providers.adapters.openai_responses import (
        stream_openai_responses,
        stream_simple_openai_responses,
    )

    return {
        "anthropic-messages": _StreamFnProvider(
            anthropic.stream_simple, anthropic.stream_simple,
            supports_web_search=True,
        ),
        "openai-completions": _StreamFnProvider(
            openai_completions.stream_simple, openai_completions.stream_simple,
            supports_idempotency_key=True,
            bounds_own_stream_idle=True,
        ),
        "google-generative-ai": _StreamFnProvider(
            google.stream_simple, google.stream_simple,
            # supports_web_search not advertised: generateContent rejects
            # google_search next to function declarations on Gemini 2.x, and
            # Google documents the combination for Gemini 3 only on the
            # Interactions API. An agent request always carries function
            # tools, so the runtime keeps the local web_search tool. The
            # builder still honours web_search=True on a request without
            # function tools (shared/google.py google_search_applies).
        ),
        "openai-responses": _StreamFnProvider(
            stream_openai_responses, stream_simple_openai_responses,
            supports_idempotency_key=True,
            supports_web_search=True,
        ),
        "openai-codex": _StreamFnProvider(
            stream_openai_codex_responses, stream_simple_openai_codex_responses,
            supports_web_search=True,
            bounds_own_stream_idle=True,
        ),
        "gemini-subscription": _StreamFnProvider(
            stream_google_gemini_cli, stream_simple_google_gemini_cli,
            # supports_web_search not advertised: generateContent rejects
            # google_search next to function declarations on Gemini 2.x, and
            # Google documents the combination for Gemini 3 only on the
            # Interactions API. An agent request always carries function
            # tools, so the runtime keeps the local web_search tool. The
            # builder still honours web_search=True on a request without
            # function tools (shared/google.py google_search_applies).
            # Its own retry loop waits silently between attempts, so it
            # bounds each attempt's read itself.
            bounds_own_stream_idle=True,
        ),
        "bedrock-converse-stream": _StreamFnProvider(
            stream_bedrock, stream_simple_bedrock,
        ),
        "azure-openai-responses": _StreamFnProvider(
            stream_azure_openai_responses, stream_simple_azure_openai_responses,
        ),
    }


def register_auth_adapters() -> None:
    """Register built-in auth callbacks without initializing provider runtime."""
    global _auth_registered
    with _lock:
        if _auth_registered:
            return
        from openprogram.models.providers.adapters.anthropic.auth_adapter import register_anthropic_auth
        from openprogram.models.providers.adapters.google_gemini_cli.auth_adapter import (
            register_gemini_cli_auth,
        )
        from openprogram.models.providers.adapters.openai_codex.auth_adapter import register_codex_auth
        from openprogram.models.providers.adapters.xai_subscription.auth_adapter import (
            register_xai_subscription_auth,
        )

        register_anthropic_auth()
        register_codex_auth()
        register_gemini_cli_auth()
        register_xai_subscription_auth()
        _auth_registered = True


_OPENAI_CHAT_CAPABILITIES = StructuredOutputCapabilities(
    native="supported",
    dialect="openai_chat",
    streaming=True,
    with_tools=True,
    strict_tool=True,
    schema_profile="openai_strict",
)
_OPENAI_RESPONSES_CAPABILITIES = StructuredOutputCapabilities(
    native="supported",
    dialect="openai_responses",
    streaming=True,
    with_tools=True,
    strict_tool=True,
    schema_profile="openai_strict",
)
_ANTHROPIC_CAPABILITIES = StructuredOutputCapabilities(
    native="supported",
    dialect="anthropic",
    streaming=True,
    with_tools=True,
    strict_tool=True,
    schema_profile="openai_strict",
)
_GOOGLE_CAPABILITIES = StructuredOutputCapabilities(
    native="supported",
    dialect="google",
    streaming=True,
    schema_profile="google_json_schema",
)
_BEDROCK_CAPABILITIES = StructuredOutputCapabilities(
    native="unknown",
    dialect="bedrock",
    streaming=True,
    schema_profile="none",
    native_model_opt_in=True,
)
_AZURE_RESPONSES_CAPABILITIES = StructuredOutputCapabilities(
    native="unknown",
    dialect="azure_openai_responses",
    streaming=True,
    strict_tool=True,
    schema_profile="openai_strict",
    native_model_opt_in=True,
)
_STRICT_TOOL_ONLY_CAPABILITIES = StructuredOutputCapabilities(
    strict_tool=True,
    schema_profile="openai_strict",
)
_UNKNOWN_CAPABILITIES = StructuredOutputCapabilities()


def register_builtins() -> None:
    """Register every built-in provider once; retry cleanly after failure."""
    global _registered
    from openprogram.models.auth.credential_provider import _load_provider_plugins

    _load_provider_plugins()
    with _lock:
        if _registered:
            return
        providers = _load_builtin_providers()
        capabilities = {
            "anthropic-messages": _ANTHROPIC_CAPABILITIES,
            "openai-completions": _OPENAI_CHAT_CAPABILITIES,
            "google-generative-ai": _GOOGLE_CAPABILITIES,
            "openai-responses": _OPENAI_RESPONSES_CAPABILITIES,
            "openai-codex": _STRICT_TOOL_ONLY_CAPABILITIES,
            "gemini-subscription": _UNKNOWN_CAPABILITIES,
            "bedrock-converse-stream": _BEDROCK_CAPABILITIES,
            "azure-openai-responses": _AZURE_RESPONSES_CAPABILITIES,
        }
        _register_builtin_api_providers(
            {
                api: (
                    ApiProviderSnapshot(
                        provider, capabilities[api],
                        bool(getattr(provider, "supports_idempotency_key", False)),
                        supports_web_search=bool(
                            getattr(provider, "supports_web_search", False)
                        ),
                    )
                    if api in capabilities
                    else provider
                )
                for api, provider in providers.items()
            }
        )
        _registered = True


__all__ = ["register_auth_adapters", "register_builtins"]
