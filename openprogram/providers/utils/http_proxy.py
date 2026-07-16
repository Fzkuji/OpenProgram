"""Outbound proxy resolution — one semantics for every provider call.

Resolution order (design record: docs/reference/design/providers/network-proxy.md):

1. ``OPENPROGRAM_PROXY_URL`` — explicit first-party override. All traffic
   routes through it (``http://``, ``https://`` or ``socks5://``);
   ``NO_PROXY`` bypasses still apply.
2. Standard environment variables, parsed by httpx's own
   ``get_environment_proxies()`` — ``http(s)_proxy`` / ``all_proxy`` /
   ``no_proxy`` in both cases, lowercase winning. Reusing httpx's parser
   (instead of a reimplementation) keeps the hardened clients byte-for-byte
   consistent with every plain ``httpx.AsyncClient()`` and SDK-built client
   in the process.

The only consumer is :mod:`.http_client`, which turns the mount map into
hardened per-pattern transports. Provider code never resolves proxies
itself.
"""

from __future__ import annotations

import os


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

    env_map: dict[str, str | None] = dict(get_environment_proxies())

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
