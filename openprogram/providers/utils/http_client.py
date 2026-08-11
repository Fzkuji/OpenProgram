"""Registry-scoped managed HTTP clients for provider SDKs."""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

from openprogram.security.safe_http import (
    SafeAsyncClient,
    configured_safe_async_client,
    configured_safe_client,
)
from openprogram.security.url_policy import OwnerURLException, normalize_origin
from . import timeouts as _timeouts


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _keepalive_socket_options() -> tuple[tuple[int, int, int], ...]:
    if not _env_flag("OPENPROGRAM_TCP_KEEPALIVE", True):
        return ()
    idle = int(_timeouts._f("OPENPROGRAM_TCP_KEEPIDLE_S", 30.0))
    interval = int(_timeouts._f("OPENPROGRAM_TCP_KEEPINTVL_S", 10.0))
    count = int(_timeouts._f("OPENPROGRAM_TCP_KEEPCNT", 3.0))
    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    keepidle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    if keepidle is not None:
        options.append((socket.IPPROTO_TCP, keepidle, idle))
    if hasattr(socket, "TCP_KEEPINTVL"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval))
    if hasattr(socket, "TCP_KEEPCNT"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, count))
    return tuple(options)


def _effective_timeout(timeout: Any):
    return timeout if timeout is not None else _timeouts.build_httpx_timeout()


def _timeout_identity(timeout: Any) -> tuple[float | None, ...]:
    effective = _effective_timeout(timeout)
    if isinstance(effective, (int, float)):
        return (float(effective),) * 4
    return (
        effective.connect,
        effective.read,
        effective.write,
        effective.pool,
    )


def build_async_client(
    *,
    consumer: str,
    configured_origin: str,
    owner_exception: OwnerURLException | None = None,
    timeout: Any = None,
    force_ipv4: bool | None = None,
) -> SafeAsyncClient:
    """Create a managed provider client with bounded streaming hardening."""
    if force_ipv4 is None:
        force_ipv4 = _env_flag("OPENPROGRAM_FORCE_IPV4", False)
    return configured_safe_async_client(
        consumer,
        configured_origin,
        owner_exception=owner_exception,
        timeout=_effective_timeout(timeout),
        overall_timeout=_timeouts.STREAM_TOTAL_TIMEOUT_S,
        local_address="0.0.0.0" if force_ipv4 else None,
        socket_options=_keepalive_socket_options(),
    )


_shared: dict[tuple[Any, ...], SafeAsyncClient] = {}
_MAX_SHARED_CLIENTS_PER_LOOP = 32


def get_shared_async_client(
    key: str,
    *,
    consumer: str,
    configured_origin: str,
    owner_exception: OwnerURLException | None = None,
    timeout: Any = None,
    force_ipv4: bool | None = None,
) -> SafeAsyncClient:
    """Reuse a managed client only within one loop, consumer, and exact origin."""
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        return build_async_client(
            consumer=consumer,
            configured_origin=configured_origin,
            owner_exception=owner_exception,
            timeout=timeout,
            force_ipv4=force_ipv4,
        )
    origin = normalize_origin(configured_origin)
    effective_force_ipv4 = (
        _env_flag("OPENPROGRAM_FORCE_IPV4", False)
        if force_ipv4 is None
        else force_ipv4
    )
    cache_key = (
        key,
        consumer,
        origin,
        owner_exception,
        _timeout_identity(timeout),
        effective_force_ipv4,
        _keepalive_socket_options(),
        loop_id,
    )
    client = _shared.get(cache_key)
    if client is None or client.is_closed:
        for stale_key in [
            cached_key
            for cached_key, cached_client in _shared.items()
            if cached_key[-1] == loop_id and cached_client.is_closed
        ]:
            _shared.pop(stale_key, None)
        loop_entries = sum(1 for cached_key in _shared if cached_key[-1] == loop_id)
        if loop_entries >= _MAX_SHARED_CLIENTS_PER_LOOP:
            raise RuntimeError("shared provider client cache limit exceeded")
        client = build_async_client(
            consumer=consumer,
            configured_origin=origin,
            owner_exception=owner_exception,
            timeout=timeout,
            force_ipv4=effective_force_ipv4,
        )
        _shared[cache_key] = client
    return client


def build_google_http_options(
    configured_origin: str,
    *,
    owner_exception: OwnerURLException | None = None,
    retry_options: Any = None,
):
    """Build Google GenAI options with managed sync and async HTTPX clients."""
    from google.genai.types import HttpOptions

    return HttpOptions(
        base_url=configured_origin,
        retry_options=retry_options,
        httpx_client=configured_safe_client(
            "provider.google.sdk",
            configured_origin,
            owner_exception=owner_exception,
        ),
        httpx_async_client=configured_safe_async_client(
            "provider.google.sdk",
            configured_origin,
            owner_exception=owner_exception,
        ),
    )


async def aclose_shared_clients() -> None:
    clients = list(_shared.values())
    _shared.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:
            pass


async def aclose_current_loop_clients() -> None:
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        return
    for cache_key in [key for key in _shared if key[-1] == loop_id]:
        client = _shared.pop(cache_key)
        try:
            await client.aclose()
        except Exception:
            pass


__all__ = [
    "aclose_current_loop_clients",
    "aclose_shared_clients",
    "build_async_client",
    "build_google_http_options",
    "get_shared_async_client",
]
