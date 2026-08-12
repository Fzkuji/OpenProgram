"""Register the built-in API providers and authentication adapters."""
from __future__ import annotations

import threading

from openprogram.providers.api_registry import register_api_providers


class _StreamFnProvider:
    """Adapt module-level stream functions to the provider interface."""

    def __init__(self, stream_fn, stream_simple_fn):
        self._stream = stream_fn
        self._stream_simple = stream_simple_fn

    def stream(self, model, context, options=None):
        return self._stream(model, context, options)

    def stream_simple(self, model, context, options=None):
        return self._stream_simple(model, context, options)


_lock = threading.RLock()
_registered = False
_auth_registered = False


def _load_builtin_providers() -> dict[str, _StreamFnProvider]:
    from openprogram.providers import anthropic, google, openai_completions
    from openprogram.providers.amazon_bedrock import stream_bedrock, stream_simple_bedrock
    from openprogram.providers.azure_openai_responses import (
        stream_azure_openai_responses,
        stream_simple_azure_openai_responses,
    )
    from openprogram.providers.google_gemini_cli import (
        stream_google_gemini_cli,
        stream_simple_google_gemini_cli,
    )
    from openprogram.providers.openai_codex.openai_codex import (
        stream_openai_codex_responses,
        stream_simple_openai_codex_responses,
    )
    from openprogram.providers.openai_responses import (
        stream_openai_responses,
        stream_simple_openai_responses,
    )

    return {
        "anthropic-messages": _StreamFnProvider(
            anthropic.stream_simple, anthropic.stream_simple
        ),
        "openai-completions": _StreamFnProvider(
            openai_completions.stream_simple, openai_completions.stream_simple
        ),
        "google-generative-ai": _StreamFnProvider(
            google.stream_simple, google.stream_simple
        ),
        "openai-responses": _StreamFnProvider(
            stream_openai_responses, stream_simple_openai_responses
        ),
        "openai-codex": _StreamFnProvider(
            stream_openai_codex_responses, stream_simple_openai_codex_responses
        ),
        "gemini-subscription": _StreamFnProvider(
            stream_google_gemini_cli, stream_simple_google_gemini_cli
        ),
        "bedrock-converse-stream": _StreamFnProvider(
            stream_bedrock, stream_simple_bedrock
        ),
        "azure-openai-responses": _StreamFnProvider(
            stream_azure_openai_responses, stream_simple_azure_openai_responses
        ),
    }


def register_auth_adapters() -> None:
    """Register built-in auth callbacks without initializing provider runtime."""
    global _auth_registered
    with _lock:
        if _auth_registered:
            return
        from openprogram.providers.anthropic.auth_adapter import register_anthropic_auth
        from openprogram.providers.google_gemini_cli.auth_adapter import (
            register_gemini_cli_auth,
        )
        from openprogram.providers.openai_codex.auth_adapter import register_codex_auth

        register_anthropic_auth()
        register_codex_auth()
        register_gemini_cli_auth()
        _auth_registered = True


def register_builtins() -> None:
    """Register every built-in provider once; retry cleanly after failure."""
    global _registered
    from openprogram.auth.credential_provider import _load_provider_plugins

    _load_provider_plugins()
    with _lock:
        if _registered:
            return
        providers = _load_builtin_providers()
        register_api_providers(providers)
        _registered = True


__all__ = ["register_auth_adapters", "register_builtins"]
