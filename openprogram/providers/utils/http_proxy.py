"""
HTTP proxy configuration for httpx-based SDK calls.

Reads HTTP_PROXY / HTTPS_PROXY environment variables and provides
a pre-configured httpx client factory that respects them.

Mirrors http-proxy.ts (which configures undici for Node.js fetch).
"""

from __future__ import annotations

import os

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


def get_proxy_url() -> str | None:
    """Return the proxy URL from environment variables, or None."""
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None


def get_proxies() -> dict[str, str] | None:
    """Return an httpx-compatible proxies dict, or None if no proxy configured."""
    proxy = get_proxy_url()
    if not proxy:
        return None
    return {"http://": proxy, "https://": proxy}


def make_httpx_client(**kwargs) -> "httpx.AsyncClient":
    """Create an httpx.AsyncClient with proxy settings applied."""
    proxy = get_proxy_url()
    if proxy:
        kwargs.setdefault("proxies", get_proxies())
    return httpx.AsyncClient(**kwargs)
