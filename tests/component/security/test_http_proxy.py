"""Proxy resolution rules — pins the invariants in
docs/reference/design/providers/requests/network-proxy.html."""

import asyncio
import ipaddress

import pytest

from openprogram.models.providers.utils.http_transport.http_client import build_async_client
from openprogram.host.security.http_proxy import get_proxy_mounts

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


def test_managed_provider_client_keeps_managed_transport_with_proxy_snapshot(proxy_env):
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    proxy_env.setenv("NO_PROXY", "example.com")
    client = build_async_client(
        consumer="provider.openai.sdk",
        configured_origin="https://api.openai.com",
    )
    try:
        assert client._transport._consumer == "provider.openai.sdk"
        assert client._transport._configured_origin == "https://api.openai.com"
        assert not client._mounts
        assert ("https://", "http://127.0.0.1:7890") in client._transport._security.service_proxy_mounts
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("bypass", ["api.openai.com", "*"])
def test_no_proxy_bypasses_audited_service(proxy_env, bypass):
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    proxy_env.setenv("NO_PROXY", bypass)
    security = replace(load_outbound_security_config("provider.configured_api", config={}), resolver=lambda *_: ("93.184.216.34",))
    transport = ManagedHTTPTransport("provider.configured_api", configured_origin="https://api.openai.com", security=security)
    try:
        assert transport._service_proxy(transport._evaluate("GET", "https://api.openai.com/v1/models")) is None
    finally:
        transport.close()


@pytest.mark.parametrize("consumer,origin", [
    ("provider.configured_api", "https://custom.test"),
    ("tool.web_search.configured_api", "https://search.custom.test"),
    ("skills.configured.catalog", "https://catalog.custom.test"),
])
def test_ambient_proxy_does_not_expand_custom_routing(proxy_env, consumer, origin):
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    security = replace(load_outbound_security_config(consumer, config={}), resolver=lambda *_: ("93.184.216.34",))
    transport = ManagedHTTPTransport(consumer, configured_origin=origin, security=security)
    try:
        decision = transport._evaluate("GET", origin)
        assert not decision.requires_proxy
        assert transport._service_proxy(decision) is None
    finally:
        transport.close()


@pytest.mark.parametrize("consumer,url", [
    ("tool.web_fetch", "https://github.com/example-org/ctxpress"),
    ("tool.image_result.download", "https://cdn.example.com/image.png"),
    ("channel.attachment.download", "https://cdn.example.com/file.pdf"),
    ("tool.web_search.fixed_api", "https://html.duckduckgo.com/html/?q=x"),
    ("updater.github", "https://api.github.com/repos/o/r/releases/latest"),
])
def test_public_consumers_follow_ambient_proxy_and_proxy_resolves(proxy_env, consumer, url):
    """A fake-IP answer is accepted only because the request goes via the proxy."""
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    security = replace(
        load_outbound_security_config(consumer, config={}),
        resolver=lambda host, _port: ("127.0.0.1",) if host == "127.0.0.1" else ("198.18.0.104",),
    )
    transport = ManagedHTTPTransport(consumer, security=security)
    try:
        decision = transport._evaluate("GET", url)
        proxy, proxy_decision = transport._service_proxy(decision)
        assert str(proxy.url) == "http://127.0.0.1:7897"
        assert proxy_decision.hostname == "127.0.0.1"
        assert decision.requires_proxy
        assert decision.resolved_ips == (ipaddress.ip_address("198.18.0.104"),)
    finally:
        transport.close()


def test_no_proxy_bypass_keeps_direct_policy_for_public_urls(proxy_env):
    """Bypassed hosts connect directly, so a fake-IP answer stays refused."""
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    from openprogram.host.security.url_policy import URLPolicyError
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    proxy_env.setenv("NO_PROXY", "github.com")
    security = replace(
        load_outbound_security_config("tool.web_fetch", config={}),
        resolver=lambda *_: ("198.18.0.104",),
    )
    transport = ManagedHTTPTransport("tool.web_fetch", security=security)
    try:
        with pytest.raises(URLPolicyError) as exc:
            transport._evaluate("GET", "https://github.com/example-org/ctxpress")
        assert exc.value.reason == "NON_GLOBAL_ADDRESS"
    finally:
        transport.close()


def test_proxied_public_url_still_refuses_private_targets(proxy_env):
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    from openprogram.host.security.url_policy import URLPolicyError
    proxy_env.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    answers = {"rebind.example": ("192.168.1.1",), "loop.example": ("127.0.0.1",)}
    security = replace(
        load_outbound_security_config("tool.web_fetch", config={}),
        resolver=lambda host, _port: answers.get(host, ("198.18.0.9",)),
    )
    transport = ManagedHTTPTransport("tool.web_fetch", security=security)
    try:
        for url in (
            "http://192.168.1.1/",
            "http://10.0.0.1/",
            "http://198.18.0.9/",
            "http://localhost/",
            "http://printer.local/",
            "http://router.lan/",
            "http://intranet/",
            "http://rebind.example/",
            "http://loop.example/",
        ):
            with pytest.raises(URLPolicyError) as exc:
                transport._evaluate("GET", url)
            assert exc.value.reason == "NON_GLOBAL_ADDRESS", url
        with pytest.raises(URLPolicyError) as exc:
            transport._evaluate("GET", "http://169.254.169.254/latest/meta-data/")
        assert exc.value.reason == "METADATA_ADDRESS"
    finally:
        transport.close()


def test_system_proxy_routes_official_provider(proxy_env):
    import httpx._utils
    from dataclasses import replace
    from openprogram.host.security.outbound_settings import load_outbound_security_config
    from openprogram.host.security.safe_http import ManagedHTTPTransport
    proxy_env.setattr(httpx._utils, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    security = replace(load_outbound_security_config("provider.configured_api", config={}), resolver=lambda host, port: ("127.0.0.1",) if host == "127.0.0.1" else ("93.184.216.34",))
    transport = ManagedHTTPTransport("provider.configured_api", configured_origin="https://api.openai.com", security=security)
    try:
        proxy, decision = transport._service_proxy(transport._evaluate("GET", "https://api.openai.com"))
        assert str(proxy.url) == "http://127.0.0.1:7897"
        assert decision.hostname == "127.0.0.1"
    finally:
        transport.close()


def test_shared_client_replaced_when_proxy_route_changes(proxy_env):
    from openprogram.models.providers.utils.http_transport.http_client import get_shared_async_client, aclose_current_loop_clients
    async def exercise():
        try:
            proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
            first = get_shared_async_client("openai-proxy-test", consumer="provider.configured_api", configured_origin="https://api.openai.com")
            proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7898")
            second = get_shared_async_client("openai-proxy-test", consumer="provider.configured_api", configured_origin="https://api.openai.com")
            assert first is not second
            assert ("https://", "http://127.0.0.1:7898") in second._transport._security.service_proxy_mounts
            await asyncio.sleep(0)
            assert first.is_closed
        finally:
            await aclose_current_loop_clients()
    asyncio.run(exercise())


def test_bypass_only_environment_preserves_system_proxy(proxy_env):
    import urllib.request
    proxy_env.setenv("NO_PROXY", "localhost,127.0.0.1")
    proxy_env.setattr(urllib.request, "getproxies_macosx_sysconf", lambda: {"https": "http://127.0.0.1:7897"}, raising=False)
    mounts = get_proxy_mounts()
    assert mounts["https://"] == "http://127.0.0.1:7897"
    assert mounts["all://localhost"] is None


def test_explicit_env_route_suppresses_system_proxy(proxy_env):
    import urllib.request
    proxy_env.setenv("HTTPS_PROXY", "http://127.0.0.1:7898")
    proxy_env.setattr(urllib.request, "getproxies_macosx_sysconf", lambda: {"https": "http://127.0.0.1:7897"}, raising=False)
    assert get_proxy_mounts()["https://"] == "http://127.0.0.1:7898"


def test_wildcard_bypass_beats_system_and_override(proxy_env):
    import urllib.request
    proxy_env.setenv("NO_PROXY", "*")
    proxy_env.setenv("OPENPROGRAM_PROXY_URL", "http://127.0.0.1:7898")
    proxy_env.setattr(urllib.request, "getproxies_macosx_sysconf", lambda: {"https": "http://127.0.0.1:7897"}, raising=False)
    assert get_proxy_mounts() is None
