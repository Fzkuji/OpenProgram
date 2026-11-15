"""Registry-scoped managed HTTP clients for provider SDKs."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from openprogram.host.security.safe_http import (
    OutboundSecurityConfig,
    SafeAsyncClient,
    _configured_security,
    configured_safe_async_client,
    configured_safe_client,
    safe_async_client,
)
from openprogram.host.security.url_policy import OwnerURLException, normalize_origin
from . import timeouts as _timeouts

_log = logging.getLogger(__name__)


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
    security: OutboundSecurityConfig | None = None,
    timeout: Any = None,
    force_ipv4: bool | None = None,
) -> SafeAsyncClient:
    """Create a managed provider client with bounded streaming hardening."""
    if force_ipv4 is None:
        force_ipv4 = _env_flag("OPENPROGRAM_FORCE_IPV4", False)
    if security is not None:
        return safe_async_client(
            consumer,
            configured_origin=normalize_origin(configured_origin),
            security=security,
            timeout=_effective_timeout(timeout),
            overall_timeout=_timeouts.STREAM_TOTAL_TIMEOUT_S,
        )
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
        # The client is discarded either way.
        _log.debug("http client close failed", exc_info=True)


# Other per-loop connections a provider keeps (the Codex WebSocket pool)
# close with the loop's shared clients; each closer runs on the owner loop.
_loop_closers: list[Callable[[], Awaitable[None]]] = []


def register_loop_closer(closer: Callable[[], Awaitable[None]]) -> None:
    """Have ``closer()`` run on a loop whenever its shared clients are closed."""
    if closer not in _loop_closers:
        _loop_closers.append(closer)


async def _close_loop_clients(loop: asyncio.AbstractEventLoop) -> None:
    if asyncio.get_running_loop() is not loop:
        raise RuntimeError("shared provider clients must close on their owner loop")
    with _shared_lock:
        cache_keys = [key for key in _shared if key[-1] is loop]
        clients = [_shared.pop(cache_key) for cache_key in cache_keys]
    for client in clients:
        await _close_client(client)
    for closer in list(_loop_closers):
        try:
            await closer()
        except Exception:
            _log.debug("provider connection close failed", exc_info=True)


def _evict_dead_loops_locked() -> None:
    """Drop the entries of loops that closed without reaping.

    A client bound to a closed loop can never be used or ``aclose``d
    again; left in the cache it would count against the limits forever,
    until every provider call failed with "cache limit exceeded". The
    loop's owner should have called :func:`shutdown_loop_clients` first;
    this is the backstop, so the entry's sockets go with the garbage
    collector instead of a clean close.
    """
    dead = [
        cached_key for cached_key in _shared
        if getattr(cached_key[-1], "is_closed", lambda: False)()
    ]
    for cached_key in dead:
        _shared.pop(cached_key, None)
        _loop_cleanup_tasks.pop(cached_key[-1], None)
    if dead:
        _log.warning(
            "dropped %d shared provider client(s) of event loops that closed "
            "without reaping them", len(dead),
        )


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
    socket_options = _keepalive_socket_options()
    transport_security = _configured_security(
        consumer,
        origin,
        owner_exception,
        local_address="0.0.0.0" if effective_force_ipv4 else None,
        socket_options=socket_options,
    )
    cache_identity = (
        key,
        consumer,
        origin,
        owner_exception,
        _timeout_identity(timeout),
        effective_force_ipv4,
        socket_options,
    )
    cache_key = (
        *cache_identity,
        transport_security,
        loop,
    )
    retired_clients: list[SafeAsyncClient] = []
    with _shared_lock:
        for stale_key in [
            cached_key
            for cached_key, cached_client in _shared.items()
            if cached_client.is_closed
        ]:
            _shared.pop(stale_key, None)
        _evict_dead_loops_locked()
        for stale_policy_key in [
            cached_key
            for cached_key in _shared
            if cached_key[-1] is loop
            and cached_key[:-2] == cache_identity
            and cached_key[-2] != transport_security
        ]:
            retired_clients.append(_shared.pop(stale_policy_key))
        client = _shared.get(cache_key)
        if client is None or client.is_closed:
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
    for retired_client in retired_clients:
        loop.create_task(_close_client(retired_client))
    if client is not None and not client.is_closed:
        return client
    try:
        client = build_async_client(
            consumer=consumer,
            configured_origin=origin,
            owner_exception=owner_exception,
            security=transport_security,
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
            timeout=_timeouts.build_httpx_timeout(),
            overall_timeout=_timeouts.STREAM_TOTAL_TIMEOUT_S,
        ),
        httpx_async_client=configured_safe_async_client(
            "provider.google.sdk",
            configured_origin,
            owner_exception=owner_exception,
            timeout=_timeouts.build_httpx_timeout(),
            overall_timeout=_timeouts.STREAM_TOTAL_TIMEOUT_S,
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


def shutdown_loop_clients(loop: asyncio.AbstractEventLoop) -> None:
    """Close the shared clients bound to ``loop`` right before it closes.

    For a loop driven by hand (``new_event_loop`` + ``run_until_complete``
    + ``close``): ``loop.close()`` cancels nothing, so the cleanup task
    that would reap the clients never runs and their entries outlive the
    loop. Best-effort: a failure here is logged, not raised.
    """
    if loop.is_closed():
        return
    try:
        loop.run_until_complete(aclose_current_loop_clients())
    except Exception:
        _log.debug("shared provider client shutdown failed", exc_info=True)


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
    "register_loop_closer",
    "aclose_current_loop_clients",
    "aclose_shared_clients",
    "shutdown_loop_clients",
    "build_async_client",
    "build_google_http_options",
    "get_shared_async_client",
]
