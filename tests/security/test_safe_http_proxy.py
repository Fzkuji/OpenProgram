from __future__ import annotations

import asyncio
import ipaddress
import socketserver
import threading

import httpcore
import pytest

from openprogram.security.safe_http import (
    ManagedHTTPTransport,
    OutboundSecurityConfig,
    PolicyProxyConfig,
    safe_async_client,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException, URLPolicyError


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
