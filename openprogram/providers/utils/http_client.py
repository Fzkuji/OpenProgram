"""Robust ``httpx.AsyncClient`` builder for LLM providers.

Centralises the connection-layer hardening every HTTP provider should
get, instead of each one calling ``httpx.AsyncClient(timeout=...)`` with
ad-hoc (often too-tight) settings. Ports the connection ideas from the
reference frameworks (see ``docs/design/providers/reliability/llm-fault-tolerance.md``):

  * **Decoupled, generous timeouts** — via :mod:`.timeouts` (connect
    bounded, body read generous). No more single-float ``timeout=120``
    that caps the streaming read.
  * **TCP keepalive** (hermes pattern) — ``SO_KEEPALIVE`` plus the
    idle/interval/count knobs so a silently-dropped connection (the
    classic VPN failure) is detected in ~60 s instead of hanging until
    the idle budget. Applied defensively: options the OS doesn't expose
    are skipped; the whole thing is disablable via env.
  * **Force-IPv4 escape hatch** — many VPNs advertise broken IPv6 and
    httpx has no Happy-Eyeballs fallback, so a connect can hang on the
    AAAA record. ``OPENPROGRAM_FORCE_IPV4=1`` binds an IPv4 source
    address, forcing the connection family to IPv4.
  * **Proxy** — honours ``HTTP(S)_PROXY`` using httpx 0.28's ``proxy=``
    kwarg (the old ``proxies=`` form was removed and silently broke).
  * **Connection reuse** — :func:`get_shared_async_client` returns a
    cached keep-alive client (keyed by name + event loop) so repeated
    calls reuse the TLS connection instead of re-handshaking every time
    — meaningful over a high-latency VPN.

Non-HTTP (CLI) providers never import this, so they pay nothing.
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any, Optional

from . import timeouts as _timeouts
from .http_proxy import get_proxy_url


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _keepalive_socket_options() -> Optional[list[tuple[int, int, int]]]:
    """``setsockopt`` tuples enabling TCP keepalive, OS-defensively.

    ``SO_KEEPALIVE`` is universal. The tuning knobs (idle before first
    probe, inter-probe interval, probe count) exist on Linux always and
    on modern macOS/Windows; each is added only when the constant is
    present. Disable entirely with ``OPENPROGRAM_TCP_KEEPALIVE=0``.

    With the defaults (idle 30 s, interval 10 s, count 3) a dead peer is
    detected in roughly 30 + 10·3 = 60 s.
    """
    if not _env_flag("OPENPROGRAM_TCP_KEEPALIVE", True):
        return None
    idle = int(_timeouts._f("OPENPROGRAM_TCP_KEEPIDLE_S", 30.0))
    intvl = int(_timeouts._f("OPENPROGRAM_TCP_KEEPINTVL_S", 10.0))
    cnt = int(_timeouts._f("OPENPROGRAM_TCP_KEEPCNT", 3.0))

    opts: list[tuple[int, int, int]] = [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
    ]
    tcp = socket.IPPROTO_TCP
    # Idle-before-first-probe: TCP_KEEPIDLE on Linux/Windows, TCP_KEEPALIVE on macOS.
    keepidle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    if keepidle is not None:
        opts.append((tcp, keepidle, idle))
    keepintvl = getattr(socket, "TCP_KEEPINTVL", None)
    if keepintvl is not None:
        opts.append((tcp, keepintvl, intvl))
    keepcnt = getattr(socket, "TCP_KEEPCNT", None)
    if keepcnt is not None:
        opts.append((tcp, keepcnt, cnt))
    return opts


def _build_transport(force_ipv4: Optional[bool]) -> Any:
    """An ``httpx.AsyncHTTPTransport`` carrying keepalive / IPv4 / proxy,
    or ``None`` when no customisation is needed (let httpx use defaults).
    """
    import httpx

    kwargs: dict[str, Any] = {}
    sock_opts = _keepalive_socket_options()
    if sock_opts:
        kwargs["socket_options"] = sock_opts
    if force_ipv4 is None:
        force_ipv4 = _env_flag("OPENPROGRAM_FORCE_IPV4", False)
    if force_ipv4:
        # Binding an IPv4 source address forces the connection family to
        # IPv4 — sidesteps a broken-IPv6 VPN that would otherwise hang on
        # the AAAA record.
        kwargs["local_address"] = "0.0.0.0"
    proxy = get_proxy_url()
    if proxy:
        kwargs["proxy"] = proxy  # httpx 0.28: `proxy=`, not the removed `proxies=`
    if not kwargs:
        return None
    return httpx.AsyncHTTPTransport(**kwargs)


def build_async_client(
    *,
    timeout: Any = None,
    force_ipv4: Optional[bool] = None,
    **client_kwargs: Any,
):
    """Create a hardened ``httpx.AsyncClient`` (caller owns its lifecycle).

    Args:
        timeout: an ``httpx.Timeout`` (defaults to :func:`timeouts.build_httpx_timeout`).
        force_ipv4: override the ``OPENPROGRAM_FORCE_IPV4`` env default.
        **client_kwargs: passed through to ``httpx.AsyncClient``.
    """
    import httpx

    kwargs = dict(client_kwargs)
    kwargs.setdefault(
        "timeout", timeout if timeout is not None else _timeouts.build_httpx_timeout()
    )
    transport = _build_transport(force_ipv4)
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# Shared (reused) clients — keyed by name + the running event loop so a
# client created under one loop is never reused under another (which httpx
# forbids). Repeated calls on the same loop reuse the keep-alive pool.
# ---------------------------------------------------------------------------

_shared: dict[tuple[str, int], Any] = {}


def get_shared_async_client(key: str = "default", **build_kwargs: Any):
    """Return a cached keep-alive client for ``key`` on the current loop.

    Do NOT ``async with`` / close the returned client — it is shared and
    lives for the process. Use it as ``client.stream(...)`` / ``client.post(...)``
    directly. Falls back to a fresh client when there is no running loop.
    """
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        # No running loop — can't safely cache; hand back a fresh client.
        return build_async_client(**build_kwargs)
    cache_key = (key, loop_id)
    client = _shared.get(cache_key)
    if client is None or client.is_closed:
        client = build_async_client(**build_kwargs)
        _shared[cache_key] = client
    return client


async def aclose_shared_clients() -> None:
    """Close all cached shared clients (best-effort, for shutdown/tests)."""
    clients = list(_shared.values())
    _shared.clear()
    for c in clients:
        try:
            await c.aclose()
        except Exception:
            pass


__all__ = [
    "build_async_client",
    "get_shared_async_client",
    "aclose_shared_clients",
]
