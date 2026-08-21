from __future__ import annotations

import gzip
import http.server
import ipaddress
import socketserver
import threading
from dataclasses import replace

import httpcore
import pytest

from openprogram.programs.tools.web import web_fetch
from openprogram.security import safe_http
from openprogram.security.safe_http import OutboundSecurityConfig


_REAL_DECISION_BACKEND = safe_http.DecisionNetworkBackend
_PUBLIC_IP = "93.184.216.34"


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.requests: list[str] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        server = self.server
        assert isinstance(server, _Server)
        server.requests.append(self.path)
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{server.port}/private")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/binary" or self.path.startswith("/private/TOKEN-PATH.png?"):
            body = b"\x89PNG\r\n"
            content_type = "image/png"
            encoding = None
        elif self.path in {"/large", "/large-more"}:
            size = web_fetch.MAX_BYTES + (1 if self.path == "/large" else 1024 * 1024)
            body = gzip.compress(b"a" * size)
            content_type = "text/plain; charset=utf-8"
            encoding = "gzip"
        elif self.path == "/declared-large":
            body = b""
            content_type = "text/plain; charset=utf-8"
            encoding = None
        else:
            body = "caf\xe9".encode("latin-1")
            content_type = "text/plain; charset=iso-8859-1"
            encoding = None
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        declared_length = (
            web_fetch.MAX_BYTES + 2 if self.path == "/declared-large" else len(body)
        )
        self.send_header("Content-Length", str(declared_length))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


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
        self._real = httpcore.SyncBackend()

    def connect_tcp(self, _host, port, **kwargs):
        stream = self._real.connect_tcp("127.0.0.1", port, **kwargs)
        return _ReportedStream(stream, self._peer)

    def connect_unix_socket(self, *args, **kwargs):
        return self._real.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._real.sleep(seconds)


class _MalformedHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = bytearray()
        while b"\r\n\r\n" not in request:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            request.extend(chunk)
        self.server.requests.append(bytes(request))
        self.request.sendall(b"TOKEN-PATH QUERY-SECRET\r\n\r\n")


class _MalformedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.requests: list[bytes] = []
        super().__init__(("127.0.0.1", 0), _MalformedHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def server():
    instance = _Server()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def malformed_server():
    instance = _MalformedServer()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def managed_web_fetch(monkeypatch: pytest.MonkeyPatch, server: _Server):
    spec = safe_http.CONSUMER_REGISTRY["tool.web_fetch"]
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry[spec.consumer] = replace(spec, allowed_ports=frozenset({server.port}))
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    monkeypatch.setattr(
        safe_http,
        "DecisionNetworkBackend",
        lambda decision: _REAL_DECISION_BACKEND(
            decision, underlying=_LoopbackBackend(str(decision.resolved_ips[0]))
        ),
    )

    def resolver(hostname: str, _port: int):
        if hostname == "public.test":
            return (_PUBLIC_IP,)
        if hostname == "private.test":
            return ("10.0.0.1",)
        if hostname == "metadata.test":
            return ("169.254.169.254",)
        return (str(ipaddress.ip_address(hostname)),)

    clients = []

    def factory(consumer: str):
        client = safe_http.safe_client(
            consumer, security=OutboundSecurityConfig(resolver=resolver)
        )
        clients.append(client)
        return client

    monkeypatch.setattr(web_fetch, "safe_client", factory, raising=False)
    return clients


def test_web_fetch_public_success_preserves_charset_and_result_shape(
    server: _Server, managed_web_fetch
) -> None:
    result = web_fetch.execute(f"http://public.test:{server.port}/text")

    assert result.startswith(f"# http://public.test:{server.port}/text\n")
    assert "café" in result
    assert server.requests == ["/text"]


def test_web_fetch_malformed_status_hides_peer_echoed_signed_url(
    malformed_server: _MalformedServer,
    managed_web_fetch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.web_fetch"]
    registry[spec.consumer] = replace(
        spec, allowed_ports=frozenset({malformed_server.port})
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)

    result = web_fetch.execute(
        f"http://public.test:{malformed_server.port}/private/TOKEN-PATH?sig=QUERY-SECRET"
    )

    assert result == (
        "Error: network error RemoteProtocolError for "
        f"http://public.test:{malformed_server.port}"
    )
    rendered = repr(result)
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "TOKEN-PATH" not in repr(managed_web_fetch[-1].audit_events)
    assert "QUERY-SECRET" not in repr(managed_web_fetch[-1].audit_events)


@pytest.mark.parametrize(
    ("hostname", "reason"),
    [
        ("private.test", "NON_GLOBAL_ADDRESS"),
        ("metadata.test", "METADATA_ADDRESS"),
    ],
)
def test_web_fetch_rejects_private_and_metadata_dns_before_connecting(
    server: _Server, managed_web_fetch, hostname: str, reason: str
) -> None:
    result = web_fetch.execute(f"http://{hostname}:{server.port}/private")

    assert result.startswith("Error: failed to fetch")
    assert reason in result
    assert server.requests == []


def test_web_fetch_rejects_private_redirect_before_second_request(
    server: _Server, managed_web_fetch
) -> None:
    result = web_fetch.execute(f"http://public.test:{server.port}/redirect-private")

    assert result.startswith("Error: failed to fetch")
    assert "NON_GLOBAL_ADDRESS" in result
    assert server.requests == ["/redirect-private"]


def test_web_fetch_rejects_binary_mime(server: _Server, managed_web_fetch) -> None:
    result = web_fetch.execute(f"http://public.test:{server.port}/binary")

    assert "Error: unsupported Content-Type 'image/png'" in result


def test_web_fetch_binary_mime_error_hides_signed_path_and_query(
    server: _Server, managed_web_fetch
) -> None:
    result = web_fetch.execute(
        f"http://public.test:{server.port}/private/TOKEN-PATH.png?sig=QUERY-SECRET"
    )

    assert result == (
        "Error: unsupported Content-Type 'image/png' for "
        f"http://public.test:{server.port}. "
        "Use `pdf` for PDFs or `image_analyze` for images."
    )
    assert "TOKEN-PATH" not in result
    assert "QUERY-SECRET" not in result


def test_web_fetch_decode_error_hides_signed_path_and_query(
    server: _Server,
    managed_web_fetch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_fetch,
        "_decode_body",
        lambda *_args: (_ for _ in ()).throw(UnicodeError("bad decode")),
    )
    result = web_fetch.execute(
        f"http://public.test:{server.port}/private/TOKEN-PATH.txt?sig=QUERY-SECRET"
    )

    assert result == (
        f"Error: cannot decode http://public.test:{server.port}: "
        "UnicodeError: bad decode"
    )
    assert "TOKEN-PATH" not in result
    assert "QUERY-SECRET" not in result


def test_web_fetch_truncates_after_five_mib_of_decoded_content(
    server: _Server, managed_web_fetch
) -> None:
    result = web_fetch.execute(
        f"http://public.test:{server.port}/large",
        format="text",
        max_chars=web_fetch.MAX_BYTES + 10,
    )

    assert result.endswith("…[response exceeded 5 MB cap, truncated]")
    body = result.split("\n\n", 1)[1].split("\n\n…[", 1)[0]
    assert body == "a" * web_fetch.MAX_BYTES


@pytest.mark.parametrize("path", ["/large-more", "/declared-large"])
def test_web_fetch_reports_managed_body_cap_as_truncation(
    server: _Server, managed_web_fetch, path: str
) -> None:
    result = web_fetch.execute(
        f"http://public.test:{server.port}{path}",
        format="text",
        max_chars=web_fetch.MAX_BYTES + 10,
    )

    assert not result.startswith("Error:")
    assert result.endswith("…[response exceeded 5 MB cap, truncated]")
