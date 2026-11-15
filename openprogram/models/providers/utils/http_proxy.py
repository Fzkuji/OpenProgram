"""Proxy configuration diagnostics for explicit policy-proxy setup.

Resolution order (design record: docs/reference/design/providers/network-proxy.html):

1. ``OPENPROGRAM_PROXY_URL`` — explicit first-party override. All traffic
   routes through it (``http://``, ``https://`` or ``socks5://``);
   ``NO_PROXY`` bypasses still apply.
2. Standard environment variables, parsed by httpx's own
   ``get_environment_proxies()`` — ``http(s)_proxy`` / ``all_proxy`` /
   ``no_proxy`` in both cases, lowercase winning. Reusing httpx's parser
   (instead of a reimplementation) keeps the hardened clients byte-for-byte
   consistent with every plain ``httpx.AsyncClient()`` and SDK-built client
   in the process.

Managed clients for public URLs, fixed audited services and audited provider
API / OAuth origins snapshot this routing map. They still validate target
policy (with the proxy as the resolver of public hostnames) and pin the proxy
socket; configured custom origins stay direct unless an explicit enforcing
policy proxy is configured.
"""

from __future__ import annotations

import os
import urllib.request


# Loopback never goes through a proxy, NO_PROXY or not: local services
# (the worker, a localhost ollama, the provider "test" button against a
# local endpoint) break behind forward proxies like Clash, which refuse
# or misroute loopback CONNECTs.
_LOOPBACK_BYPASS: dict[str, None] = {
    "all://localhost": None,
    "all://127.0.0.1": None,
    "all://[::1]": None,
}


def get_proxy_mounts() -> dict[str, str | None] | None:
    """httpx mount map: URL pattern -> proxy URL (``None`` = bypass).

    Returns ``None`` when no proxy is configured at all, so callers can
    skip building mounts entirely.
    """
    # httpx's parser is private but stable across our supported range
    # (>=0.27); invariant #3 in the design doc says mirror it, never
    # invent different semantics, if it ever moves.
    from httpx._utils import get_environment_proxies

    # HTTPX represents NO_PROXY=* as an empty map; keep that explicit bypass
    # distinct from "no routes" before merging system settings or an override.
    bypasses = urllib.request.getproxies_environment().get("no", "")
    if "*" in (value.strip() for value in bypasses.split(",")):
        return None
    env_map: dict[str, str | None] = dict(get_environment_proxies())
    # urllib's getproxies() suppresses system fallback even when the only
    # environment entry is NO_PROXY. Worker launchers commonly set that entry
    # to protect localhost, so merge the system routes in this one case.
    if not any(value is not None for value in env_map.values()):
        system_reader = getattr(urllib.request, "getproxies_macosx_sysconf", None)
        if system_reader is None:
            system_reader = getattr(urllib.request, "getproxies_registry", None)
        if system_reader is not None:
            system_map = {
                f"{scheme}://": value
                for scheme, value in system_reader().items()
                if scheme in {"http", "https", "all"}
            }
            env_map = {**system_map, **env_map}

    override = os.environ.get("OPENPROGRAM_PROXY_URL", "").strip()
    if override:
        # Keep the NO_PROXY bypass entries (value None), replace the rest.
        mounts: dict[str, str | None] = {
            pattern: None for pattern, url in env_map.items() if url is None
        }
        mounts["all://"] = override
        return {**_LOOPBACK_BYPASS, **mounts}

    if not env_map:
        return None
    return {**_LOOPBACK_BYPASS, **env_map}


def proxy_for_url(url: str) -> str | None:
    """The proxy :func:`get_proxy_mounts` routes ``url`` through, or ``None``.

    For a transport that is not an httpx client (the Codex WebSocket), so it
    goes the way an httpx request to the same URL would: the most specific
    matching pattern wins, as httpx orders its mounts. A ``ws(s)://`` URL is
    matched as the ``http(s)://`` URL of its handshake.
    """
    from httpx import URL
    from httpx._utils import URLPattern

    mounts = get_proxy_mounts()
    if not mounts:
        return None
    if url.startswith("wss://"):
        url = "https://" + url[len("wss://"):]
    elif url.startswith("ws://"):
        url = "http://" + url[len("ws://"):]
    target = URL(url)
    for pattern in sorted(URLPattern(key) for key in mounts):
        if pattern.matches(target):
            return mounts[pattern.pattern]
    return None
