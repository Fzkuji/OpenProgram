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
    supports_idempotency_key: bool = False


# One registry entry owns both the provider and its verified capabilities.
_registry: dict[str, ApiProviderSnapshot | ApiProvider] = {}
_original_registry: dict[str, ApiProviderSnapshot | ApiProvider] = {}
_provider_transform: Callable[[str, ApiProvider], ApiProvider] | None = None
_registry_lock = threading.RLock()
_audited_accounting: dict[int, set[str]] = {}
_audited_originals: dict[str, ApiProvider] = {}


def _entry(value: ApiProviderSnapshot | ApiProvider) -> ApiProviderSnapshot:
    if isinstance(value, ApiProviderSnapshot):
        return value
    return ApiProviderSnapshot(
        value, StructuredOutputCapabilities(),
        bool(getattr(value, "supports_idempotency_key", False)),
    )


def _rebuild_audited_accounting() -> None:
    _audited_accounting.clear()
    for api, original in _audited_originals.items():
        source = _original_registry.get(api)
        if source is None or _entry(source).provider is not original:
            continue
        current = _registry.get(api)
        for provider in (original, _entry(current).provider if current is not None else None):
            if provider is not None:
                _audited_accounting.setdefault(id(provider), set()).add(api)


def register_api_provider(
    api: Api,
    provider: ApiProvider,
    structured_output: StructuredOutputCapabilities | None = None,
    *,
    supports_idempotency_key: bool | None = None,
) -> None:
    """Register an API provider implementation."""
    with _registry_lock:
        original = ApiProviderSnapshot(
            provider,
            structured_output or StructuredOutputCapabilities(),
            (
                bool(getattr(provider, "supports_idempotency_key", False))
                if supports_idempotency_key is None
                else supports_idempotency_key
            ),
        )
        registered = (
            _provider_transform(api, provider)
            if _provider_transform is not None
            else provider
        )
        _audited_originals.pop(api, None)
        _original_registry[api] = original
        _registry[api] = ApiProviderSnapshot(
            registered, original.structured_output, original.supports_idempotency_key,
        )
        _rebuild_audited_accounting()


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
                ApiProviderSnapshot(
                    provider, original.structured_output,
                    original.supports_idempotency_key,
                )
                if isinstance(value, ApiProviderSnapshot)
                else provider
            )
        for api in originals:
            _audited_originals.pop(api, None)
        _original_registry.update(originals)
        _registry.update(transformed)
        _rebuild_audited_accounting()


def _register_builtin_api_providers(
    providers: dict[Api, ApiProviderSnapshot | ApiProvider],
) -> None:
    """Register audited built-ins; public registration never grants this capability."""
    with _registry_lock:
        originals = dict(providers)
        transformed = {}
        audited = {}
        for api, value in originals.items():
            original = _entry(value)
            provider = original.provider
            if provider is None:
                continue
            audited[api] = provider
            registered = (
                _provider_transform(api, provider)
                if _provider_transform is not None
                else provider
            )
            transformed[api] = (
                ApiProviderSnapshot(
                    registered, original.structured_output,
                    original.supports_idempotency_key,
                )
                if isinstance(value, ApiProviderSnapshot)
                else registered
            )
        _original_registry.update(originals)
        _registry.update(transformed)
        _audited_originals.update(audited)
        _rebuild_audited_accounting()


def has_audited_accounting(provider: ApiProvider | None, api: str | None) -> bool:
    """Whether this concrete registered implementation has audited metering."""
    if provider is None or not api:
        return False
    with _registry_lock:
        return api in _audited_accounting.get(id(provider), set())


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
            False,
        )
    return get_api_provider_snapshot(model.api) or ApiProviderSnapshot(
        None,
        StructuredOutputCapabilities(),
        False,
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
            registered = transform(api, original.provider)
            transformed[api] = (
                ApiProviderSnapshot(
                    registered, original.structured_output,
                    original.supports_idempotency_key,
                )
                if isinstance(value, ApiProviderSnapshot)
                else registered
            )
        _registry.clear()
        _registry.update(transformed)
        _rebuild_audited_accounting()
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
            registered = transform(api, original.provider)
            transformed[api] = (
                ApiProviderSnapshot(
                    registered, original.structured_output,
                    original.supports_idempotency_key,
                )
                if isinstance(value, ApiProviderSnapshot)
                else registered
            )
        _registry.clear()
        _registry.update(transformed)
        _rebuild_audited_accounting()
        _provider_transform = transform
