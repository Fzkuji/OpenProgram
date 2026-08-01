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


def register_api_provider(api: Api, provider: ApiProvider) -> None:
    """Register an API provider implementation."""
    _registry[api] = provider


def get_api_provider(api: Api) -> ApiProvider | None:
    """Get a registered API provider."""
    return _registry.get(api)
