"""
API provider registration system — mirrors packages/ai/src/api-registry.ts
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from .types import Api, Context, Model, SimpleStreamOptions, StreamOptions


class ApiProvider(Protocol):
    """Protocol for API provider implementations."""

    @property
    def requires_credentials(self) -> bool:
        """Whether the stream chokepoint should resolve an API key."""
        ...

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
_original_registry: dict[str, ApiProvider] = {}
_provider_transform: Callable[[str, ApiProvider], ApiProvider] | None = None
_registry_lock = threading.RLock()


def register_api_provider(api: Api, provider: ApiProvider) -> None:
    """Register an API provider implementation."""
    with _registry_lock:
        _original_registry[api] = provider
        _registry[api] = (
            _provider_transform(api, provider) if _provider_transform is not None else provider
        )


def get_api_provider(api: Api) -> ApiProvider | None:
    """Get a registered API provider."""
    with _registry_lock:
        return _registry.get(api)


def configure_provider_transform(
    transform: Callable[[str, ApiProvider], ApiProvider],
) -> None:
    """Install the process-wide wrapper for existing and future providers."""
    global _provider_transform
    with _registry_lock:
        if _provider_transform is transform:
            return
        if _provider_transform is not None:
            raise RuntimeError("provider transform is already configured")
        transformed = {
            api: transform(api, provider)
            for api, provider in _original_registry.items()
        }
        _registry.clear()
        _registry.update(transformed)
        _provider_transform = transform


def _replace_provider_transform(
    transform: Callable[[str, ApiProvider], ApiProvider],
) -> None:
    """Atomically replace the transform for a fail-closed startup fallback."""
    global _provider_transform
    with _registry_lock:
        transformed = {
            api: transform(api, provider)
            for api, provider in _original_registry.items()
        }
        _registry.clear()
        _registry.update(transformed)
        _provider_transform = transform
