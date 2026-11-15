from __future__ import annotations

import asyncio
import ipaddress
import socketserver
import threading

import httpcore
import pytest

from openprogram.host.security.safe_http import (
    ManagedHTTPTransport,
    OutboundSecurityConfig,
    PolicyProxyConfig,
    safe_async_client,
    safe_client,
)
from openprogram.host.security.url_policy import OwnerURLException, URLPolicyError


class _FailingProxyPool:
    def __init__(self, calls):
        self.calls = calls

    def handle_request(self, _request):
        self.calls.append("proxy")
        raise httpcore.ProxyError("proxy failed")

    def close(self):
        pass


class _ClosableStream:
    def __init__(self, stream):
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self):
        pass


class _AsyncClosableStream:
    async def __aiter__(self):
        yield b"ok"

    async def aclose(self):
        pass


class _RecordingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.request_lines = []
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        assert isinstance(server, _RecordingProxy)
        request_line = self.rfile.readline().decode("ascii").strip()
        server.request_lines.append(request_line)
        while self.rfile.readline() not in {b"", b"\r\n"}:
            pass
        self.wfile.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            b"Content-Length: 2\r\nConnection: close\r\n\r\nok"
        )


def test_environment_proxies_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    transport = ManagedHTTPTransport(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    decision = transport._evaluate("GET", "https://public.test/resource")
    pool = transport._pool(decision)
    try:
        assert type(pool) is httpcore.ConnectionPool
    finally:
        transport.close()


def test_proxy_without_target_policy_declaration_is_rejected():
    with pytest.raises(URLPolicyError) as exc:
        OutboundSecurityConfig(
            policy_proxy=PolicyProxyConfig(
                "https://proxy.test", enforces_target_policy=False
            )
        )

    assert exc.value.reason == "POLICY_PROXY_ENFORCEMENT_REQUIRED"


def test_target_policy_is_evaluated_before_proxy_resolution(monkeypatch):
    resolutions = []

    def resolver(host, port):
        resolutions.append((host, port))
        if host == "public.test":
            return ("127.0.0.1",)
        return ("93.184.216.34",)

    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=resolver,
            policy_proxy=PolicyProxyConfig(
                "https://proxy.test", enforces_target_policy=True
            ),
        ),
    )

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/resource")

    assert exc.value.reason == "NON_GLOBAL_ADDRESS"
    assert resolutions == [("public.test", 443)]


def test_policy_proxy_uses_a_separately_constrained_decision_and_audits_delegation(
    monkeypatch,
):
    captured = []

    class _ProxyPool:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def handle_request(self, _request):
            response = httpcore.Response(
                200,
                headers=[(b"content-type", b"text/plain")],
                content=b"ok",
            )
            response.stream = _ClosableStream(response.stream)
            return response

        def close(self):
            pass

    monkeypatch.setattr(httpcore, "HTTPProxy", _ProxyPool)
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",),
            policy_proxy=PolicyProxyConfig(
                "https://proxy.test", enforces_target_policy=True
            ),
        ),
    )

    with client:
        response = client.get("https://public.test/resource")

    backend = captured[0]["network_backend"]
    assert response.content == b"ok"
    assert backend._decision.origin == "https://proxy.test"
    assert backend._decision.consumer == "runtime.local_probe"
    assert all(event.delegated_to_policy_proxy for event in client.audit_events)
    assert "public.test" in repr(client.audit_events)
    assert "resource" not in repr(client.audit_events)


def test_async_policy_proxy_uses_a_separately_constrained_decision(monkeypatch):
    captured = []

    class _AsyncProxyPool:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def handle_async_request(self, _request):
            response = httpcore.Response(200, content=_empty_async())
            response.headers = [(b"content-type", b"text/plain")]
            response.stream = _AsyncClosableStream()
            return response

        async def aclose(self):
            pass

    monkeypatch.setattr(httpcore, "AsyncHTTPProxy", _AsyncProxyPool)

    async def exercise():
        client = safe_async_client(
            "tool.web_fetch",
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("93.184.216.34",),
                policy_proxy=PolicyProxyConfig(
                    "https://proxy.test", enforces_target_policy=True
                ),
            ),
        )
        async with client:
            response = await client.get("https://public.test/resource")
        return response

    assert asyncio.run(exercise()).content == b"ok"
    assert captured[0]["network_backend"]._decision.origin == "https://proxy.test"


def test_real_policy_proxy_receives_absolute_form_without_direct_fallback():
    proxy = _RecordingProxy()
    try:
        proxy_url = f"http://proxy.test:{proxy.server_address[1]}"
        target_origin = "http://target.test:12345"
        exception = OwnerURLException(
            consumer="runtime.local_probe",
            network=ipaddress.ip_network("127.0.0.0/8"),
        )
        client = safe_client(
            "runtime.local_probe",
            configured_origin=target_origin,
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("127.0.0.1",),
                owner_exceptions=(exception,),
                policy_proxy=PolicyProxyConfig(proxy_url, enforces_target_policy=True),
            ),
        )

        with client:
            response = client.get(f"{target_origin}/resource")

        assert response.content == b"ok"
        assert proxy.request_lines == ["GET http://target.test:12345/resource HTTP/1.1"]
    finally:
        proxy.close()


def test_proxy_failure_has_no_direct_fallback(monkeypatch):
    calls = []
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",),
            policy_proxy=PolicyProxyConfig(
                "https://proxy.test", enforces_target_policy=True
            ),
        ),
    )
    monkeypatch.setattr(
        client._transport, "_pool", lambda _decision: _FailingProxyPool(calls)
    )

    with client, pytest.raises(Exception) as exc:
        client.get("https://public.test/resource")

    assert type(exc.value).__name__ == "ProxyError"
    assert calls == ["proxy"]


async def _empty_async():
    if False:
        yield b""


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("proxy_scheme", ["http", "https", "socks5"])
def test_official_service_requests_use_ambient_proxy(monkeypatch, asynchronous, proxy_scheme):
    from openprogram.host.security.safe_http import configured_safe_client, configured_safe_async_client
    from openprogram.state.settings import load_outbound_security_config

    calls = []
    monkeypatch.setenv("OPENPROGRAM_PROXY_URL", f"{proxy_scheme}://user:password@127.0.0.1:7897")

    class Pool:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def handle_request(self, request):
            calls.append(request)
            response = httpcore.Response(200, headers=[(b"content-type", b"text/plain")], content=b"ok")
            response.stream = _ClosableStream(response.stream)
            return response

        async def handle_async_request(self, request):
            calls.append(request)
            response = httpcore.Response(200, headers=[(b"content-type", b"text/plain")])
            response.stream = _AsyncClosableStream()
            return response

        def close(self):
            pass

        async def aclose(self):
            pass

    pool_name = ("Async" if asynchronous else "") + ("SOCKSProxy" if proxy_scheme == "socks5" else "HTTPProxy")
    monkeypatch.setattr(httpcore, pool_name, Pool)
    # Freeze a deterministic target resolution while using the real config and proxy resolver.
    from dataclasses import replace
    import openprogram.state.settings as schema
    monkeypatch.setattr(schema, "load_outbound_security_config", lambda consumer: replace(
        load_outbound_security_config(consumer, config={}), resolver=lambda host, port: ("127.0.0.1",) if host == "127.0.0.1" else ("93.184.216.34",)
    ))
    origin = "https://chatgpt.com"
    consumer = "provider.configured_api" if asynchronous else "webui.model_listing.configured"
    if asynchronous:
        async def exercise():
            async with configured_safe_async_client(consumer, origin) as client:
                return await client.get(origin + "/backend-api/codex/models", headers={"Authorization": "Bearer service-secret", "Proxy-Authorization": "hostile"})
        response = asyncio.run(exercise())
    else:
        with configured_safe_client(consumer, origin) as client:
            response = client.get(origin + "/backend-api/codex/models", headers={"Authorization": "Bearer service-secret", "Proxy-Authorization": "hostile"})
    assert response.content == b"ok"
    assert calls[0]["proxy_url"] == f"{proxy_scheme}://127.0.0.1:7897"
    assert calls[0]["proxy_auth"] == (b"user", b"password")
    assert calls[0]["network_backend"]._decision.resolved_ips == (ipaddress.ip_address("127.0.0.1"),)
    assert calls[1].url.host == b"chatgpt.com"
    assert (b"Authorization", b"Bearer service-secret") in calls[1].headers
    assert not any(name.lower() == b"proxy-authorization" for name, _ in calls[1].headers)
    assert response.extensions["url_decision"].origin == origin


def test_official_service_proxy_rejects_metadata_peer_before_connect(monkeypatch):
    from dataclasses import replace
    import openprogram.state.settings as schema
    original = schema.load_outbound_security_config
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:7897")
    monkeypatch.setattr(schema, "load_outbound_security_config", lambda consumer: replace(
        original(consumer, config={}), resolver=lambda host, port: ("169.254.169.254",) if host == "proxy.test" else ("93.184.216.34",)
    ))
    from openprogram.host.security.safe_http import configured_safe_client
    with configured_safe_client("webui.model_listing.configured", "https://chatgpt.com") as client:
        with pytest.raises(URLPolicyError) as exc:
            client.get("https://chatgpt.com/backend-api/codex/models")
    assert exc.value.reason == "METADATA_ADDRESS"


def test_official_service_proxy_failure_has_no_direct_fallback(monkeypatch):
    from dataclasses import replace
    import httpx
    import openprogram.state.settings as schema
    original = schema.load_outbound_security_config
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(schema, "load_outbound_security_config", lambda consumer: replace(
        original(consumer, config={}), resolver=lambda host, port: ("127.0.0.1",) if host == "127.0.0.1" else ("93.184.216.34",)
    ))
    monkeypatch.setattr(httpcore, "HTTPProxy", lambda **kwargs: _FailingProxyPool(calls))
    monkeypatch.setattr(httpcore, "ConnectionPool", lambda **kwargs: pytest.fail("unexpected direct fallback"))
    from openprogram.host.security.safe_http import configured_safe_client
    with configured_safe_client("webui.model_listing.configured", "https://chatgpt.com") as client:
        with pytest.raises(httpx.ProxyError):
            client.get("https://chatgpt.com/backend-api/codex/models")
    assert calls == ["proxy"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("proxy_url,port", [
    ("https://proxy.test", 443),
    ("https://proxy.test:443", 443),
    ("https://proxy.test:7897", 7897),
    ("socks5://proxy.test", 1080),
    ("socks5h://proxy.test", 1080),
])
def test_real_proxy_handshake_uses_correct_ports_and_tls_hostnames(monkeypatch, asynchronous, proxy_url, port):
    from dataclasses import replace
    import openprogram.state.settings as schema
    from openprogram.host.security.safe_http import configured_safe_client, configured_safe_async_client
    original = schema.load_outbound_security_config
    calls = []
    connections = []

    class Stream(httpcore.NetworkStream):
        def __init__(self):
            handshake = (
                [b"\x05\x00", b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"]
                if proxy_url.startswith("socks5")
                else [b"HTTP/1.1 200 Connection established\r\n\r\n"]
            )
            self.responses = handshake + [
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok",
            ]

        def read(self, max_bytes, timeout=None):
            return self.responses.pop(0) if self.responses else b""

        def write(self, data, timeout=None):
            pass

        def close(self):
            pass

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.check_hostname
            calls.append(server_hostname)
            return self

        def get_extra_info(self, name):
            return ("127.0.0.1", port) if name == "server_addr" else None

    class AsyncStream(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.stream = Stream()

        async def read(self, max_bytes, timeout=None):
            return self.stream.read(max_bytes, timeout)

        async def write(self, data, timeout=None):
            self.stream.write(data, timeout)

        async def aclose(self):
            self.stream.close()

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.stream.start_tls(ssl_context, server_hostname, timeout)
            return self

        def get_extra_info(self, name):
            return self.stream.get_extra_info(name)

    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            return Stream()

    class AsyncBackend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            return AsyncStream()

    monkeypatch.setattr(httpcore, "SyncBackend", Backend)
    monkeypatch.setattr(httpcore, "AnyIOBackend", AsyncBackend)
    monkeypatch.setattr(schema, "load_outbound_security_config", lambda consumer: replace(
        original(consumer, config={}), service_proxy_mounts=(("https://", proxy_url),),
        resolver=lambda host, port: ("127.0.0.1",) if host == "proxy.test" else ("93.184.216.34",),
    ))
    origin = "https://chatgpt.com"
    if asynchronous:
        async def exercise():
            async with configured_safe_async_client("provider.configured_api", origin) as client:
                request = client.build_request("GET", origin, extensions={"sni_hostname": "hostile.test"})
                return await client.send(request)
        response = asyncio.run(exercise())
    else:
        with configured_safe_client("webui.model_listing.configured", origin) as client:
            request = client.build_request("GET", origin, extensions={"sni_hostname": "hostile.test"})
            response = client.send(request)
    assert response.text == "ok"
    assert connections == [("127.0.0.1", port)]
    assert calls == (["chatgpt.com"] if proxy_url.startswith("socks5") else ["proxy.test", "chatgpt.com"])


def _ambient_security(monkeypatch, resolver):
    """Real routing snapshot from the environment, deterministic target DNS."""
    from dataclasses import replace
    import openprogram.state.settings as schema

    original = schema.load_outbound_security_config
    monkeypatch.setattr(
        schema,
        "load_outbound_security_config",
        lambda consumer: replace(original(consumer, config={}), resolver=resolver),
    )


def _fake_ip_resolver(host, _port):
    # A Clash-style fake-IP DNS answers every public name from 198.18.0.0/15.
    return ("127.0.0.1",) if host == "127.0.0.1" else ("198.18.0.104",)


def test_web_fetch_behind_fake_ip_proxy_reaches_target_through_real_proxy(monkeypatch):
    from openprogram.programs.tools.web import web_fetch

    proxy = _RecordingProxy()
    try:
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_address[1]}")
        _ambient_security(monkeypatch, _fake_ip_resolver)
        monkeypatch.setattr(
            httpcore,
            "ConnectionPool",
            lambda **_kwargs: pytest.fail("public URL must not bypass the proxy"),
        )

        result = web_fetch.execute(url="http://github.com/example-org/ctxpress", format="text")

        assert not result.startswith("Error"), result
        assert "ok" in result
        assert proxy.request_lines == ["GET http://github.com/example-org/ctxpress HTTP/1.1"]
    finally:
        proxy.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_https_public_url_tunnels_by_hostname_through_pinned_proxy(monkeypatch, asynchronous):
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    _ambient_security(monkeypatch, _fake_ip_resolver)

    class Pool:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def handle_request(self, request):
            calls.append(request)
            response = httpcore.Response(200, headers=[(b"content-type", b"text/plain")], content=b"ok")
            response.stream = _ClosableStream(response.stream)
            return response

        async def handle_async_request(self, request):
            calls.append(request)
            response = httpcore.Response(200, headers=[(b"content-type", b"text/plain")])
            response.stream = _AsyncClosableStream()
            return response

        def close(self):
            pass

        async def aclose(self):
            pass

    monkeypatch.setattr(httpcore, "AsyncHTTPProxy" if asynchronous else "HTTPProxy", Pool)
    url = "https://github.com/example-org/ctxpress"
    if asynchronous:
        async def exercise():
            async with safe_async_client("tool.web_fetch") as client:
                return await client.get(url)
        response = asyncio.run(exercise())
    else:
        with safe_client("tool.web_fetch") as client:
            response = client.get(url)

    assert response.content == b"ok"
    assert calls[0]["proxy_url"] == "http://127.0.0.1:7897"
    assert calls[0]["network_backend"]._decision.resolved_ips == (ipaddress.ip_address("127.0.0.1"),)
    assert calls[1].url.host == b"github.com"
    assert response.extensions["url_decision"].requires_proxy


def test_proxy_resolved_decision_never_opens_a_direct_socket():
    from openprogram.host.security.safe_http import DecisionNetworkBackend
    from openprogram.host.security.url_policy import URLTrustClass, evaluate_url

    decision = evaluate_url(
        "tool.web_fetch",
        "GET",
        "https://github.com/",
        trust_class=URLTrustClass.UNTRUSTED_PUBLIC,
        resolver=lambda *_: ("198.18.0.104",),
        proxy_resolves=True,
    )
    transport = ManagedHTTPTransport(
        "tool.web_fetch",
        security=OutboundSecurityConfig(resolver=lambda *_: ("198.18.0.104",)),
    )
    try:
        with pytest.raises(URLPolicyError) as exc:
            transport._pool(decision)
        assert exc.value.reason == "PROXY_ROUTE_REQUIRED"
        assert transport.audit_events[-1].reason == "PROXY_ROUTE_REQUIRED"
    finally:
        transport.close()

    with pytest.raises(URLPolicyError) as exc:
        DecisionNetworkBackend(decision).connect_tcp("github.com", 443)
    assert exc.value.reason == "PROXY_ROUTE_REQUIRED"
