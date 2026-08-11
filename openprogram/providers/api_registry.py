"""
API provider registration system — mirrors packages/ai/src/api-registry.ts
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Protocol

from .structured_output import StructuredOutputCapabilities
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


@dataclass(frozen=True)
class _ApiProviderEntry:
    provider: ApiProvider
    structured_output: StructuredOutputCapabilities


# One registry entry owns both the provider and its verified capabilities.
_registry: dict[str, _ApiProviderEntry | ApiProvider] = {}
_original_registry: dict[str, _ApiProviderEntry | ApiProvider] = {}
_provider_transform: Callable[[str, ApiProvider], ApiProvider] | None = None
_registry_lock = threading.RLock()


def _entry(value: _ApiProviderEntry | ApiProvider) -> _ApiProviderEntry:
    if isinstance(value, _ApiProviderEntry):
        return value
    return _ApiProviderEntry(value, StructuredOutputCapabilities())


def register_api_provider(
    api: Api,
    provider: ApiProvider,
    structured_output: StructuredOutputCapabilities | None = None,
) -> None:
    """Register an API provider implementation."""
    with _registry_lock:
        original = _ApiProviderEntry(
            provider,
            structured_output or StructuredOutputCapabilities(),
        )
        _original_registry[api] = original
        registered = (
            _provider_transform(api, provider) if _provider_transform is not None else provider
        )
        _registry[api] = _ApiProviderEntry(registered, original.structured_output)


def get_api_provider(api: Api) -> ApiProvider | None:
    """Get a registered API provider."""
    with _registry_lock:
        value = _registry.get(api)
        return _entry(value).provider if value is not None else None


def get_structured_output_capabilities(api: Api) -> StructuredOutputCapabilities:
    """Return verified API capabilities, defaulting to fail-closed unknown."""
    with _registry_lock:
        value = _registry.get(api)
        if value is None:
            return StructuredOutputCapabilities()
        return _entry(value).structured_output


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
        transformed = {}
        for api, value in _original_registry.items():
            original = _entry(value)
            transformed[api] = _ApiProviderEntry(
                transform(api, original.provider),
                original.structured_output,
            )
        _registry.clear()
        _registry.update(transformed)
        _provider_transform = transform
