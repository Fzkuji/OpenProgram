"""Registry-scoped managed HTTP clients for provider SDKs."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
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
_MAX_SHARED_CLIENTS_TOTAL = 32
_loop_cleanup_tasks: dict[asyncio.AbstractEventLoop, asyncio.Task[None]] = {}
_shared_reservations: set[tuple[Any, ...]] = set()
_shared_lock = threading.Lock()


async def _close_client(client: SafeAsyncClient) -> None:
    try:
        await client.aclose()
    except Exception:
        pass


async def _close_loop_clients(loop: asyncio.AbstractEventLoop) -> None:
    if asyncio.get_running_loop() is not loop:
        raise RuntimeError("shared provider clients must close on their owner loop")
    with _shared_lock:
        cache_keys = [key for key in _shared if key[-1] is loop]
        clients = [_shared.pop(cache_key) for cache_key in cache_keys]
    for client in clients:
        await _close_client(client)


async def _cleanup_loop(loop: asyncio.AbstractEventLoop) -> None:
    try:
        await asyncio.Future()
    finally:
        await _close_loop_clients(loop)
        with _shared_lock:
            if _loop_cleanup_tasks.get(loop) is asyncio.current_task():
                _loop_cleanup_tasks.pop(loop, None)


def _ensure_loop_cleanup_locked(loop: asyncio.AbstractEventLoop) -> None:
    task = _loop_cleanup_tasks.get(loop)
    if task is None or task.done():
        _loop_cleanup_tasks[loop] = loop.create_task(_cleanup_loop(loop))


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
        loop = asyncio.get_running_loop()
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
        _env_flag("OPENPROGRAM_FORCE_IPV4", False) if force_ipv4 is None else force_ipv4
    )
    cache_key = (
        key,
        consumer,
        origin,
        owner_exception,
        _timeout_identity(timeout),
        effective_force_ipv4,
        _keepalive_socket_options(),
        loop,
    )
    with _shared_lock:
        for stale_key in [
            cached_key
            for cached_key, cached_client in _shared.items()
            if cached_client.is_closed
        ]:
            _shared.pop(stale_key, None)
        client = _shared.get(cache_key)
        if client is not None and not client.is_closed:
            return client
        loop_entries = sum(
            1
            for cached_key in (*_shared, *_shared_reservations)
            if cached_key[-1] is loop
        )
        if loop_entries >= _MAX_SHARED_CLIENTS_PER_LOOP:
            raise RuntimeError("shared provider client cache limit exceeded")
        if len(_shared) + len(_shared_reservations) >= _MAX_SHARED_CLIENTS_TOTAL:
            raise RuntimeError("shared provider client cache limit exceeded")
        _shared_reservations.add(cache_key)
    try:
        client = build_async_client(
            consumer=consumer,
            configured_origin=origin,
            owner_exception=owner_exception,
            timeout=timeout,
            force_ipv4=effective_force_ipv4,
        )
    except BaseException:
        with _shared_lock:
            _shared_reservations.discard(cache_key)
        raise
    with _shared_lock:
        _shared_reservations.discard(cache_key)
        _shared[cache_key] = client
        _ensure_loop_cleanup_locked(loop)
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
    loop = asyncio.get_running_loop()
    with _shared_lock:
        if any(cache_key[-1] is not loop for cache_key in _shared):
            raise RuntimeError(
                "shared provider clients must close on their owner loops"
            )
    await _close_loop_clients(loop)


async def aclose_current_loop_clients() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _shared_lock:
        cleanup_task = _loop_cleanup_tasks.get(loop)
    if cleanup_task is not None and cleanup_task is not asyncio.current_task():
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
    await _close_loop_clients(loop)
    with _shared_lock:
        if _loop_cleanup_tasks.get(loop) is cleanup_task:
            _loop_cleanup_tasks.pop(loop, None)


__all__ = [
    "aclose_current_loop_clients",
    "aclose_shared_clients",
    "build_async_client",
    "build_google_http_options",
    "get_shared_async_client",
]
