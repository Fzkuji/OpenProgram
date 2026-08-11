from __future__ import annotations

import asyncio
import ipaddress
import socketserver
import threading

import anyio
import httpcore
import httpx
import pytest

from openprogram.security.safe_http import (
    AsyncDecisionNetworkBackend,
    AsyncManagedHTTPTransport,
    ManagedHTTPTransport,
    OutboundSecurityConfig,
    DecisionNetworkBackend,
    SafeAsyncClient,
    _AsyncResponseStream,
    _ResponseStream,
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
        self.closed_connections = 0
        self._closed_condition = threading.Condition()
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

    def wait_for_closed_connections(self, count: int, timeout: float = 1.0) -> bool:
        with self._closed_condition:
            return self._closed_condition.wait_for(
                lambda: self.closed_connections >= count, timeout=timeout
            )


class _HTTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _RecordingHTTPServer)
        self.request.settimeout(10)
        try:
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
                server.accepted_targets.append(self.request.getsockname()[0])
                server.host_headers.append(headers["host"])
                self.wfile.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: keep-alive\r\n\r\nok"
                )
                self.wfile.flush()
        finally:
            with server._closed_condition:
                server.closed_connections += 1
                server._closed_condition.notify_all()


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


@pytest.mark.parametrize("async_backend", ["asyncio", "trio"])
def test_async_transport_uses_public_anyio_backend_on_supported_runtimes(
    http_server, async_backend
):
    url = f"http://safe.test:{http_server.port}/resource"

    async def exercise():
        transport = AsyncManagedHTTPTransport(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(lambda _hostname, _port: ("127.0.0.1",)),
        )
        decision = transport._evaluate("GET", url)
        network_backend = AsyncDecisionNetworkBackend(decision)
        assert type(network_backend._underlying) is httpcore.AnyIOBackend
        async with SafeAsyncClient(transport) as client:
            response = await client.get(url)
        assert response.content == b"ok"

    anyio.run(exercise, backend=async_backend)


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


class _FailingResponseStream:
    def __init__(self, error: Exception):
        self.error = error

    def __iter__(self):
        raise self.error
        yield b""  # pragma: no cover

    def close(self) -> None:
        pass


class _AsyncFailingResponseStream:
    def __init__(self, error: Exception):
        self.error = error

    async def __aiter__(self):
        raise self.error
        yield b""  # pragma: no cover

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize(
    ("core_error", "expected_type"),
    [
        (httpcore.ReadTimeout("late timeout"), httpx.ReadTimeout),
        (httpcore.ReadError("late read"), httpx.ReadError),
    ],
)
def test_sync_response_stream_maps_late_httpcore_errors(core_error, expected_type):
    stream = _ResponseStream(_FailingResponseStream(core_error))

    with pytest.raises(expected_type) as exc:
        list(stream)

    assert type(exc.value) is expected_type


@pytest.mark.parametrize(
    ("core_error", "expected_type"),
    [
        (httpcore.ReadTimeout("late timeout"), httpx.ReadTimeout),
        (httpcore.ReadError("late read"), httpx.ReadError),
    ],
)
def test_async_response_stream_maps_late_httpcore_errors(core_error, expected_type):
    async def exercise():
        stream = _AsyncResponseStream(_AsyncFailingResponseStream(core_error))
        with pytest.raises(expected_type) as exc:
            [chunk async for chunk in stream]
        return exc.value

    error = asyncio.run(exercise())

    assert type(error) is expected_type


def test_response_stream_preserves_url_policy_error():
    error = URLPolicyError("PEER_ADDRESS_MISMATCH", "https://safe.test")
    stream = _ResponseStream(_FailingResponseStream(error))

    with pytest.raises(URLPolicyError) as exc:
        list(stream)

    assert exc.value is error


def test_async_response_stream_preserves_url_policy_error():
    async def exercise():
        error = URLPolicyError("PEER_ADDRESS_MISMATCH", "https://safe.test")
        stream = _AsyncResponseStream(_AsyncFailingResponseStream(error))
        with pytest.raises(URLPolicyError) as exc:
            [chunk async for chunk in stream]
        return error, exc.value

    error, raised = asyncio.run(exercise())
    assert raised is error


class _FailingPool:
    def __init__(self, error: Exception):
        self.error = error

    def handle_request(self, _request):
        raise self.error


class _AsyncFailingPool:
    def __init__(self, error: Exception):
        self.error = error

    async def handle_async_request(self, _request):
        raise self.error


@pytest.mark.parametrize(
    ("core_error", "expected_type"),
    [
        (httpcore.ConnectTimeout("connect timeout"), httpx.ConnectTimeout),
        (httpcore.RemoteProtocolError("bad protocol"), httpx.RemoteProtocolError),
    ],
)
def test_sync_transport_maps_request_httpcore_errors(core_error, expected_type):
    url = "http://safe.test:8080/resource"
    transport = ManagedHTTPTransport(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(lambda _hostname, _port: ("127.0.0.1",)),
    )
    transport._pool = lambda _decision: _FailingPool(core_error)
    try:
        with pytest.raises(expected_type) as exc:
            transport.handle_request(httpx.Request("GET", url))
    finally:
        transport.close()

    assert type(exc.value) is expected_type


@pytest.mark.parametrize(
    ("core_error", "expected_type"),
    [
        (httpcore.ConnectTimeout("connect timeout"), httpx.ConnectTimeout),
        (httpcore.RemoteProtocolError("bad protocol"), httpx.RemoteProtocolError),
    ],
)
def test_async_transport_maps_request_httpcore_errors(core_error, expected_type):
    async def exercise():
        url = "http://safe.test:8080/resource"
        transport = AsyncManagedHTTPTransport(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(lambda _hostname, _port: ("127.0.0.1",)),
        )
        transport._pool = lambda _decision: _AsyncFailingPool(core_error)
        try:
            with pytest.raises(expected_type) as exc:
                await transport.handle_async_request(httpx.Request("GET", url))
        finally:
            await transport.aclose()
        return exc.value

    error = asyncio.run(exercise())
    assert type(error) is expected_type


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


def test_sync_dns_churn_closes_idle_pool_without_closing_active_response(http_server):
    answers = iter((("127.0.0.1", "127.0.0.2"), ("127.0.0.1",)))
    url = f"http://safe.test:{http_server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(lambda _hostname, _port: next(answers)),
    ) as client:
        first = client.send(client.build_request("GET", url), stream=True)
        second = client.get(url)

        assert second.content == b"ok"
        assert http_server.wait_for_closed_connections(1)
        assert first.read() == b"ok"
        assert http_server.wait_for_closed_connections(2)


def test_async_dns_churn_closes_idle_pool_without_closing_active_response(http_server):
    answers = iter((("127.0.0.1", "127.0.0.2"), ("127.0.0.1",)))
    url = f"http://safe.test:{http_server.port}/resource"

    async def exercise():
        async with safe_async_client(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(lambda _hostname, _port: next(answers)),
        ) as client:
            first = await client.send(client.build_request("GET", url), stream=True)
            second = await client.get(url)

            assert second.content == b"ok"
            assert http_server.wait_for_closed_connections(1)
            assert await first.aread() == b"ok"
            assert http_server.wait_for_closed_connections(2)

    asyncio.run(exercise())


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
