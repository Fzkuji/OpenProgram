from __future__ import annotations

import asyncio
import ipaddress
import socketserver
import threading

import httpcore
import pytest

from openprogram.security.safe_http import (
    AsyncDecisionNetworkBackend,
    ManagedHTTPTransport,
    OutboundSecurityConfig,
    DecisionNetworkBackend,
    safe_async_client,
    safe_client,
)
from openprogram.security.url_policy import (
    OwnerURLException,
    URLDecision,
    URLPolicyError,
    URLTrustClass,
)


class _RecordingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.accepted_targets: list[str] = []
        self.host_headers: list[str] = []
        super().__init__(("127.0.0.1", 0), _HTTPHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _HTTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(2)
        while True:
            try:
                request_line = self.rfile.readline()
            except TimeoutError:
                return
            if not request_line:
                return
            headers: dict[str, str] = {}
            while True:
                line = self.rfile.readline()
                if line in {b"", b"\r\n"}:
                    break
                name, value = line.decode("latin-1").split(":", 1)
                headers[name.lower()] = value.strip()
            server = self.server
            assert isinstance(server, _RecordingHTTPServer)
            server.accepted_targets.append(self.request.getsockname()[0])
            server.host_headers.append(headers["host"])
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                b"Connection: keep-alive\r\n\r\nok"
            )
            self.wfile.flush()


@pytest.fixture
def http_server():
    server = _RecordingHTTPServer()
    try:
        yield server
    finally:
        server.close()


def _security(resolver, *, retries: int = 0, proxy_identity: str | None = None):
    return OutboundSecurityConfig(
        resolver=resolver,
        owner_exceptions=(
            OwnerURLException(
                consumer="runtime.local_probe",
                origin=None,
                network=ipaddress.ip_network("127.0.0.0/8"),
            ),
        ),
        retries=retries,
        policy_proxy_identity=proxy_identity,
    )


def test_sync_request_resolves_once_and_connects_to_the_approved_peer(http_server):
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",) if len(calls) == 1 else ("10.0.0.1",)

    url = f"http://safe.test:{http_server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(resolver),
    ) as client:
        response = client.get(url)

    decision = response.extensions["url_decision"]
    assert response.text == "ok"
    assert calls == [("safe.test", http_server.port)]
    assert http_server.accepted_targets == ["127.0.0.1"]
    assert http_server.host_headers == [f"safe.test:{http_server.port}"]
    assert (
        ipaddress.ip_address(http_server.accepted_targets[0]) in decision.resolved_ips
    )


def test_async_request_resolves_once_and_connects_to_the_approved_peer(http_server):
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",) if len(calls) == 1 else ("10.0.0.1",)

    url = f"http://safe.test:{http_server.port}/resource"

    async def exercise():
        async with safe_async_client(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(resolver),
        ) as client:
            return await client.get(url)

    response = asyncio.run(exercise())
    decision = response.extensions["url_decision"]
    assert response.text == "ok"
    assert calls == [("safe.test", http_server.port)]
    assert http_server.accepted_targets == ["127.0.0.1"]
    assert http_server.host_headers == [f"safe.test:{http_server.port}"]
    assert (
        ipaddress.ip_address(http_server.accepted_targets[0]) in decision.resolved_ips
    )


def test_sync_request_replaces_a_hostile_host_header(http_server):
    url = f"http://safe.test:{http_server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(lambda _hostname, _port: ("127.0.0.1",)),
    ) as client:
        response = client.get(url, headers={"Host": "hostile.test"})

    assert response.status_code == 200
    assert http_server.host_headers == [f"safe.test:{http_server.port}"]


def test_async_request_replaces_a_hostile_host_header(http_server):
    url = f"http://safe.test:{http_server.port}/resource"

    async def exercise():
        async with safe_async_client(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(lambda _hostname, _port: ("127.0.0.1",)),
        ) as client:
            return await client.get(url, headers={"Host": "hostile.test"})

    response = asyncio.run(exercise())
    assert response.status_code == 200
    assert http_server.host_headers == [f"safe.test:{http_server.port}"]


class _ReportedPeerStream(httpcore.NetworkStream):
    def __init__(self, peer: str):
        self.peer = peer
        self.closed = False

    def get_extra_info(self, info: str):
        return (self.peer, 443) if info == "server_addr" else None

    def close(self) -> None:
        self.closed = True


class _ReportedPeerBackend(httpcore.NetworkBackend):
    def __init__(self, peer: str):
        self.stream = _ReportedPeerStream(peer)

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        return self.stream


class _AsyncReportedPeerStream(httpcore.AsyncNetworkStream):
    def __init__(self, peer: str):
        self.peer = peer
        self.closed = False

    def get_extra_info(self, info: str):
        return (self.peer, 443) if info == "server_addr" else None

    async def aclose(self) -> None:
        self.closed = True


class _AsyncReportedPeerBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, peer: str):
        self.stream = _AsyncReportedPeerStream(peer)

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        return self.stream


def _decision() -> URLDecision:
    return URLDecision(
        consumer="tool.web_fetch",
        method="GET",
        normalized_url="https://safe.test/resource",
        origin="https://safe.test",
        hostname="safe.test",
        port=443,
        resolved_ips=(ipaddress.ip_address("93.184.216.34"),),
        trust_class=URLTrustClass.UNTRUSTED_PUBLIC,
    )


def test_sync_backend_rejects_a_peer_outside_the_decision():
    underlying = _ReportedPeerBackend("10.0.0.1")
    backend = DecisionNetworkBackend(_decision(), underlying=underlying)

    with pytest.raises(URLPolicyError) as exc:
        backend.connect_tcp("safe.test", 443)

    assert exc.value.reason == "PEER_ADDRESS_MISMATCH"
    assert exc.value.safe_url == "https://safe.test"
    assert underlying.stream.closed


def test_async_backend_rejects_a_peer_outside_the_decision():
    async def exercise():
        underlying = _AsyncReportedPeerBackend("10.0.0.1")
        backend = AsyncDecisionNetworkBackend(_decision(), underlying=underlying)
        with pytest.raises(URLPolicyError) as exc:
            await backend.connect_tcp("safe.test", 443)
        return exc.value, underlying.stream.closed

    error, closed = asyncio.run(exercise())
    assert error.reason == "PEER_ADDRESS_MISMATCH"
    assert error.safe_url == "https://safe.test"
    assert closed


def test_retries_stay_with_the_decision_and_a_new_request_gets_a_new_decision(
    http_server,
):
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        if len(calls) == 1:
            return ("127.0.0.2", "127.0.0.1")
        return ("127.0.0.1",)

    url = f"http://safe.test:{http_server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(resolver, retries=1),
    ) as client:
        first = client.get(url)
        second = client.get(url)

    assert calls == [
        ("safe.test", http_server.port),
        ("safe.test", http_server.port),
    ]
    assert tuple(map(str, first.extensions["url_decision"].resolved_ips)) == (
        "127.0.0.2",
        "127.0.0.1",
    )
    assert tuple(map(str, second.extensions["url_decision"].resolved_ips)) == (
        "127.0.0.1",
    )
    assert http_server.accepted_targets == ["127.0.0.1", "127.0.0.1"]


@pytest.mark.parametrize(
    ("consumer", "origin", "answers", "proxy_identity"),
    [
        ("runtime.local_probe", "http://safe.test", ("127.0.0.1",), None),
        ("mcp.configured.http", "http://safe.test", ("127.0.0.1",), None),
        ("runtime.local_probe", "http://other.test", ("127.0.0.1",), None),
        ("runtime.local_probe", "http://safe.test", ("127.0.0.2",), None),
        ("runtime.local_probe", "http://safe.test", ("127.0.0.1",), "proxy-a"),
    ],
)
def test_pool_key_isolates_consumer_scope_origin_addresses_and_proxy_identity(
    consumer, origin, answers, proxy_identity
):
    port = 8080
    normalized_origin = f"{origin}:{port}"
    exceptions = (
        OwnerURLException(
            consumer=consumer,
            network=ipaddress.ip_network("127.0.0.0/8"),
        ),
    )
    transport = ManagedHTTPTransport(
        consumer,
        configured_origin=normalized_origin,
        security=OutboundSecurityConfig(
            resolver=lambda _hostname, _port: answers,
            owner_exceptions=exceptions,
            policy_proxy_identity=proxy_identity,
        ),
    )
    decision = transport._evaluate("GET", f"{normalized_origin}/resource")
    try:
        key = transport._pool_key(decision)
    finally:
        transport.close()

    expected = (
        consumer,
        URLTrustClass.CONFIGURED_SERVICE.value,
        normalized_origin,
        answers,
        proxy_identity,
    )
    assert key == expected


def test_unknown_registry_key_is_rejected_by_both_factories():
    with pytest.raises(KeyError):
        safe_client("missing.consumer")
    with pytest.raises(KeyError):
        safe_async_client("missing.consumer")


def test_sync_public_consumer_ignores_an_unrelated_configured_exception():
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",)

    security = OutboundSecurityConfig(
        resolver=resolver,
        owner_exceptions=(
            OwnerURLException(
                consumer="runtime.local_probe",
                network=ipaddress.ip_network("127.0.0.0/8"),
            ),
        ),
    )
    with safe_client("tool.web_fetch", security=security) as client:
        with pytest.raises(URLPolicyError) as exc:
            client.get("http://safe.test/resource")

    assert exc.value.reason == "NON_GLOBAL_ADDRESS"
    assert calls == [("safe.test", 80)]


def test_async_public_consumer_ignores_an_unrelated_configured_exception():
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",)

    security = OutboundSecurityConfig(
        resolver=resolver,
        owner_exceptions=(
            OwnerURLException(
                consumer="runtime.local_probe",
                network=ipaddress.ip_network("127.0.0.0/8"),
            ),
        ),
    )

    async def exercise():
        async with safe_async_client("tool.web_fetch", security=security) as client:
            with pytest.raises(URLPolicyError) as exc:
                await client.get("http://safe.test/resource")
        return exc.value

    error = asyncio.run(exercise())
    assert error.reason == "NON_GLOBAL_ADDRESS"
    assert calls == [("safe.test", 80)]
