"""Proxy resolution rules — pins the invariants in
docs/reference/design/providers/network-proxy.md §5."""

import asyncio

import httpx
import pytest

from openprogram.providers.utils.http_client import build_async_client
from openprogram.providers.utils.http_proxy import get_proxy_mounts

_PROXY_VARS = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
    "NO_PROXY", "no_proxy",
    "OPENPROGRAM_PROXY_URL",
)


@pytest.fixture
def proxy_env(monkeypatch):
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    # urllib's getproxies() (which httpx delegates to) falls back to the OS
    # proxy settings (macOS System Preferences / Windows registry) when the
    # env vars are empty. Pin it to env-only so the tests are deterministic
    # on machines with a system-level proxy. httpx binds the name at import
    # (`from urllib.request import getproxies`), so patch httpx's copy.
    import urllib.request

    import httpx._utils
    monkeypatch.setattr(
        httpx._utils, "getproxies", urllib.request.getproxies_environment
    )
    return monkeypatch


def test_no_proxy_configured_means_none(proxy_env):
    assert get_proxy_mounts() is None


def test_standard_env_vars_including_all_proxy(proxy_env):
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    proxy_env.setenv("ALL_PROXY", "socks5://127.0.0.1:7891")
    mounts = get_proxy_mounts()
    assert mounts["https://"] == "http://127.0.0.1:7890"
    assert mounts["all://"] == "socks5://127.0.0.1:7891"


def test_no_proxy_produces_bypass_entries(proxy_env):
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    proxy_env.setenv("NO_PROXY", "example.com,localhost")
    mounts = get_proxy_mounts()
    assert mounts["all://*example.com"] is None
    assert mounts["all://localhost"] is None


def test_override_replaces_proxies_but_keeps_bypasses(proxy_env):
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    proxy_env.setenv("NO_PROXY", "localhost")
    proxy_env.setenv("OPENPROGRAM_PROXY_URL", "socks5://10.0.0.1:1080")
    mounts = get_proxy_mounts()
    assert mounts["all://"] == "socks5://10.0.0.1:1080"
    assert "https://" not in mounts  # env proxy replaced by the override
    assert mounts["all://localhost"] is None  # bypass survives


def test_hardened_client_routes_per_url(proxy_env):
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    proxy_env.setenv("NO_PROXY", "example.com")
    client = build_async_client()
    try:
        proxied = client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
        bypassed = client._transport_for_url(httpx.URL("https://sub.example.com/x"))
        assert proxied is not client._transport  # goes through the proxy mount
        assert bypassed is client._transport     # NO_PROXY host stays direct
    finally:
        asyncio.run(client.aclose())
