from __future__ import annotations

import asyncio
import http.server
import socket
import socketserver
import threading
from dataclasses import replace
from types import MappingProxyType

import httpcore
import pytest

from openprogram.security import safe_http
from openprogram.security.safe_http import (
    OutboundSecurityConfig,
    PolicyProxyConfig,
    configured_safe_client,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException, URLPolicyError


class _Handler(http.server.BaseHTTPRequestHandler):
    def _reply(self) -> None:
        self.server.requests.append((self.command, self.path, dict(self.headers)))
        if self.path == "/cross-origin":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"ok": true}' if self.path.startswith("/api") else b"managed"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply
    do_PATCH = _reply

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _ReportedStream(httpcore.NetworkStream):
    def __init__(self, stream: httpcore.NetworkStream, peer: str):
        self._stream = stream
        self._peer = peer

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(self, *args, **kwargs):
        return self._stream.start_tls(*args, **kwargs)

    def get_extra_info(self, info: str):
        if info == "server_addr":
            return (self._peer, 80)
        return self._stream.get_extra_info(info)


class _LoopbackBackend(httpcore.NetworkBackend):
    def __init__(self, peer: str):
        self._peer = peer
        self._backend = httpcore.SyncBackend()

    def connect_tcp(self, _host, port, **kwargs):
        stream = self._backend.connect_tcp("127.0.0.1", port, **kwargs)
        return _ReportedStream(stream, self._peer)

    def connect_unix_socket(self, *args, **kwargs):
        return self._backend.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


def _public_socket_mapping(monkeypatch, port: int) -> None:
    original = safe_http.DecisionNetworkBackend
    monkeypatch.setattr(
        safe_http,
        "DecisionNetworkBackend",
        lambda decision: original(
            decision,
            underlying=_LoopbackBackend(str(decision.resolved_ips[0])),
        ),
    )


def _local_fixed_consumer(monkeypatch, consumer: str, origin: str) -> None:
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry[consumer]
    registry[consumer] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({int(origin.rsplit(":", 1)[1])}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))


def test_public_cdn_download_is_a_real_managed_socket_request(
    monkeypatch, local_server, tmp_path
):
    port = local_server.server_address[1]
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry["tool.web_fetch"] = replace(
        registry["tool.web_fetch"], allowed_ports=frozenset({port})
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    _public_socket_mapping(monkeypatch, port)
    url = f"http://cdn.example.test:{port}/asset"

    with safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(resolver=lambda *_args: ("93.184.216.34",)),
    ) as client:
        destination = client.download(url, tmp_path / "asset.bin")

    assert destination.read_bytes() == b"managed"
    method, path, headers = local_server.requests[0]
    assert (method, path) == ("GET", "/asset")
    assert headers["Host"] == f"cdn.example.test:{port}"


@pytest.mark.parametrize(
    ("consumer", "hostname"),
    [("skills.github.catalog", "github.com"), ("updater.github", "api.github.com")],
)
def test_github_catalog_and_update_redirects_remain_inside_declared_policy(
    monkeypatch, local_server, consumer, hostname
):
    port = local_server.server_address[1]
    origin = f"http://{hostname}:{port}"
    _local_fixed_consumer(monkeypatch, consumer, origin)
    _public_socket_mapping(monkeypatch, port)

    with safe_client(
        consumer,
        security=OutboundSecurityConfig(resolver=lambda *_args: ("93.184.216.34",)),
    ) as client:
        response = client.get(origin + "/redirect")

    assert response.content == b"managed"
    assert [path for _method, path, _headers in local_server.requests] == [
        "/redirect",
        "/final",
    ]


def test_telegram_fixed_api_preserves_its_exact_origin_and_credentials(
    monkeypatch, local_server
):
    port = local_server.server_address[1]
    origin = f"http://api.telegram.org:{port}"
    _local_fixed_consumer(monkeypatch, "channel.telegram.api", origin)
    _public_socket_mapping(monkeypatch, port)

    with safe_client(
        "channel.telegram.api",
        security=OutboundSecurityConfig(resolver=lambda *_args: ("93.184.216.34",)),
    ) as client:
        response = client.post(
            origin + "/api/getMe", headers={"Authorization": "Bearer local-test"}
        )

    assert response.json() == {"ok": True}
    assert local_server.requests[0][2]["Authorization"] == "Bearer local-test"
    assert local_server.requests[0][2]["Host"] == f"api.telegram.org:{port}"


@pytest.mark.parametrize("consumer", ["provider.openai.sdk", "mcp.configured.http"])
def test_configured_local_provider_and_mcp_reach_only_the_exact_local_origin(
    local_server, consumer
):
    origin = f"http://127.0.0.1:{local_server.server_address[1]}"
    exception = OwnerURLException(consumer=consumer, origin=origin)

    with configured_safe_client(consumer, origin, owner_exception=exception) as client:
        response = client.post(
            origin + "/api/request", headers={"Authorization": "Bearer local-test"}
        )

    assert response.json() == {"ok": True}
    assert local_server.requests[0][2]["Authorization"] == "Bearer local-test"


def test_private_enterprise_exception_is_limited_to_its_exact_origin(local_server):
    origin = f"http://127.0.0.1:{local_server.server_address[1]}"
    exception = OwnerURLException(consumer="provider.configured_api", origin=origin)
    with configured_safe_client(
        "provider.configured_api", origin, owner_exception=exception
    ) as client:
        assert client.get(origin + "/api/status").status_code == 200
        with pytest.raises(URLPolicyError) as raised:
            client.get("http://127.0.0.1:1/api/status")

    assert raised.value.reason == "CONFIGURED_ORIGIN_MISMATCH"
    assert len(local_server.requests) == 1


class _OutageHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.server.requests += 1


class _OutageProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.requests = 0
        super().__init__(("127.0.0.1", 0), _OutageHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


def test_policy_proxy_outage_does_not_fallback_to_a_direct_target_connection():
    proxy = _OutageProxy()
    try:
        proxy_origin = f"http://proxy.test:{proxy.server_address[1]}"
        with safe_client(
            "tool.web_fetch",
            security=OutboundSecurityConfig(
                resolver=lambda host, _port: (
                    ("127.0.0.1",) if host == "proxy.test" else ("93.184.216.34",)
                ),
                owner_exceptions=(
                    OwnerURLException(
                        consumer="runtime.local_probe", origin=proxy_origin
                    ),
                ),
                policy_proxy=PolicyProxyConfig(
                    proxy_origin, enforces_target_policy=True
                ),
            ),
        ) as client:
            with pytest.raises(Exception):
                client.get("http://target.example.test/no-fallback")
    finally:
        proxy.close()

    assert proxy.requests == 1


def test_provider_failover_clients_do_not_share_pool_or_credentials(local_server):
    second = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    second.requests = []
    thread = threading.Thread(target=second.serve_forever, daemon=True)
    thread.start()
    first_origin = f"http://127.0.0.1:{local_server.server_address[1]}"
    second_origin = f"http://127.0.0.1:{second.server_address[1]}"
    local_server.redirect_to = second_origin + "/api/leak"

    async def exercise():
        from openprogram.providers.utils.http_client import (
            aclose_current_loop_clients,
            get_shared_async_client,
        )

        first = get_shared_async_client(
            "first",
            consumer="provider.openai.sdk",
            configured_origin=first_origin,
            owner_exception=OwnerURLException(
                consumer="provider.openai.sdk", origin=first_origin
            ),
        )
        second_client = get_shared_async_client(
            "second",
            consumer="provider.openai.sdk",
            configured_origin=second_origin,
            owner_exception=OwnerURLException(
                consumer="provider.openai.sdk", origin=second_origin
            ),
        )
        with pytest.raises(URLPolicyError) as raised:
            await first.get(
                first_origin + "/cross-origin",
                headers={"Authorization": "Bearer first"},
            )
        await second_client.get(
            second_origin + "/api/second", headers={"Authorization": "Bearer second"}
        )
        assert first is not second_client
        assert first._transport is not second_client._transport
        await aclose_current_loop_clients()
        return raised.value

    try:
        error = asyncio.run(exercise())
        assert error.reason == "REDIRECT_ORIGIN_FORBIDDEN"
        assert local_server.requests[0][2]["Authorization"] == "Bearer first"
        assert second.requests[0][2]["Authorization"] == "Bearer second"
        assert [request[1] for request in second.requests] == ["/api/second"]
    finally:
        second.shutdown()
        second.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_ipv4_and_ipv6_loopback_callbacks_are_exact(host):
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET

    class Server(http.server.ThreadingHTTPServer):
        address_family = family

    try:
        server = Server((host, 0), _Handler)
    except OSError:
        pytest.skip(f"{host} loopback is unavailable")
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = (
        f"http://[{host}]:{server.server_address[1]}"
        if host == "::1"
        else f"http://{host}:{server.server_address[1]}"
    )
    try:
        with safe_client(
            "mcp.loopback.callback",
            callback_origin=origin,
            security=OutboundSecurityConfig(resolver=lambda *_args: (host,)),
        ) as client:
            assert client.get(origin + "/api/callback").status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(server.requests) == 1
