"""
API provider registration system — mirrors packages/ai/src/api-registry.ts
"""
from __future__ import annotations

from typing import Any, Protocol

from .types import Api, Context, Model, SimpleStreamOptions, StreamOptions


class ApiProvider(Protocol):
    """Protocol for API provider implementations."""

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> Any:  # Returns AssistantMessageEventStream
        ...

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> Any:  # Returns AssistantMessageEventStream
        ...


# Registry mapping API type → provider implementation
_registry: dict[str, ApiProvider] = {}


def register_api_provider(api: Api, provider: ApiProvider, source_id: str = "builtin") -> None:
    """Register an API provider implementation."""
    _registry[api] = provider
    _registry[f"{api}:source"] = source_id  # type: ignore[assignment]


def get_api_provider(api: Api) -> ApiProvider | None:
    """Get a registered API provider."""
    return _registry.get(api)  # type: ignore[return-value]


def unregister_api_providers(source_id: str) -> None:
    """Remove all providers registered under a given source ID."""
    to_remove = [
        key for key in list(_registry.keys())
        if not key.endswith(":source") and _registry.get(f"{key}:source") == source_id
    ]
    for key in to_remove:
        _registry.pop(key, None)
        _registry.pop(f"{key}:source", None)


def clear_api_providers() -> None:
    """Clear all registered API providers."""
    _registry.clear()


def get_api_providers() -> list[ApiProvider]:
    """Get all registered API providers."""
    return [
        provider for key, provider in _registry.items()
        if not key.endswith(":source")
    ]
