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
class ApiProviderSnapshot:
    """One atomic, immutable view of a registered provider contract."""

    provider: ApiProvider | None
    structured_output: StructuredOutputCapabilities


# One registry entry owns both the provider and its verified capabilities.
_registry: dict[str, ApiProviderSnapshot | ApiProvider] = {}
_original_registry: dict[str, ApiProviderSnapshot | ApiProvider] = {}
_provider_transform: Callable[[str, ApiProvider], ApiProvider] | None = None
_registry_lock = threading.RLock()


def _entry(value: ApiProviderSnapshot | ApiProvider) -> ApiProviderSnapshot:
    if isinstance(value, ApiProviderSnapshot):
        return value
    return ApiProviderSnapshot(value, StructuredOutputCapabilities())


def register_api_provider(
    api: Api,
    provider: ApiProvider,
    structured_output: StructuredOutputCapabilities | None = None,
) -> None:
    """Register an API provider implementation."""
    with _registry_lock:
        original = ApiProviderSnapshot(
            provider,
            structured_output or StructuredOutputCapabilities(),
        )
        _original_registry[api] = original
        registered = (
            _provider_transform(api, provider)
            if _provider_transform is not None
            else provider
        )
        _registry[api] = ApiProviderSnapshot(registered, original.structured_output)


def get_api_provider_snapshot(api: Api) -> ApiProviderSnapshot | None:
    """Atomically capture a provider and the capabilities registered with it."""
    from .initialization import initialize_provider_runtime

    initialize_provider_runtime()
    with _registry_lock:
        value = _registry.get(api)
        return _entry(value) if value is not None else None


def register_api_providers(
    providers: dict[Api, ApiProviderSnapshot | ApiProvider],
) -> None:
    """Atomically publish a batch of API providers."""
    with _registry_lock:
        originals = dict(providers)
        transformed = {}
        for api, value in originals.items():
            original = _entry(value)
            provider = original.provider
            if provider is not None and _provider_transform is not None:
                provider = _provider_transform(api, provider)
            transformed[api] = (
                ApiProviderSnapshot(provider, original.structured_output)
                if isinstance(value, ApiProviderSnapshot)
                else provider
            )
        _original_registry.update(originals)
        _registry.update(transformed)


def get_api_provider(api: Api) -> ApiProvider | None:
    """Get a registered API provider."""
    snapshot = get_api_provider_snapshot(api)
    return snapshot.provider if snapshot is not None else None


def get_structured_output_capabilities(api: Api) -> StructuredOutputCapabilities:
    """Return verified API capabilities, defaulting to fail-closed unknown."""
    snapshot = get_api_provider_snapshot(api)
    if snapshot is None:
        return StructuredOutputCapabilities()
    return snapshot.structured_output


def resolve_api_provider_snapshot(model: Model) -> ApiProviderSnapshot:
    """Capture the concrete adapter contract used for one request."""
    if model.provider == "callable" and model.api == "completion":
        return ApiProviderSnapshot(
            None,
            StructuredOutputCapabilities(
                native="supported",
                dialect="callable",
                streaming=True,
                with_tools=False,
                schema_profile="none",
            ),
        )
    return get_api_provider_snapshot(model.api) or ApiProviderSnapshot(
        None,
        StructuredOutputCapabilities(),
    )


def resolve_structured_output_capabilities(
    model: Model,
) -> StructuredOutputCapabilities:
    """Resolve the capability contract for the concrete adapter model."""
    return resolve_api_provider_snapshot(model).structured_output


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
            transformed[api] = ApiProviderSnapshot(
                transform(api, original.provider),
                original.structured_output,
            )
        _registry.clear()
        _registry.update(transformed)
        _provider_transform = transform


def _replace_provider_transform(
    transform: Callable[[str, ApiProvider], ApiProvider],
) -> None:
    """Atomically replace the transform for a fail-closed startup fallback."""
    global _provider_transform
    with _registry_lock:
        transformed = {}
        for api, value in _original_registry.items():
            original = _entry(value)
            transformed[api] = ApiProviderSnapshot(
                transform(api, original.provider),
                original.structured_output,
            )
        _registry.clear()
        _registry.update(transformed)
        _provider_transform = transform
