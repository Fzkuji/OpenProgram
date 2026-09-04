from __future__ import annotations

import asyncio
import ipaddress
import socketserver
import threading
from collections import deque

import httpcore
import httpx
import pytest

from openprogram.security.safe_http import (
    OutboundSecurityConfig,
    safe_async_client,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException, URLPolicyError


class _ScriptedPool:
    def __init__(self, script, requests, streams=None):
        self._script = script
        self._requests = requests
        self._streams = streams

    def handle_request(self, request):
        self._requests.append(request)
        status, headers, content = self._script.popleft()
        response = httpcore.Response(status, headers=headers, content=content)
        response.stream = _ClosableStream(response.stream)
        if self._streams is not None:
            self._streams.append(response.stream)
        return response

    def close(self):
        pass


class _ClosableStream:
    def __init__(self, stream):
        self._stream = stream
        self.closed = False

    def __iter__(self):
        yield from self._stream

    def close(self):
        self.closed = True


class _AsyncClosableStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class _AsyncScriptedPool:
    def __init__(self, script, requests, streams=None):
        self._script = script
        self._requests = requests
        self._streams = streams

    async def handle_async_request(self, request):
        self._requests.append(request)
        status, headers, content = self._script.popleft()
        response = httpcore.Response(status, headers=headers, content=_empty_async())
        response.stream = _AsyncClosableStream([content])
        if self._streams is not None:
            self._streams.append(response.stream)
        return response

    async def aclose(self):
        pass


async def _empty_async():
    if False:
        yield b""


class _RedirectServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.paths = []
        super().__init__(("127.0.0.1", 0), _RedirectHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _RedirectHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        assert isinstance(server, _RedirectServer)
        path = self.rfile.readline().decode("ascii").split()[1]
        server.paths.append(path)
        while self.rfile.readline() not in {b"", b"\r\n"}:
            pass
        if path == "/start":
            self.wfile.write(
                b"HTTP/1.1 302 Found\r\nLocation: /next\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
        else:
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\nok"
            )


def _scripted_client(monkeypatch, consumer, origin, script):
    requests = []
    client = safe_client(
        consumer,
        configured_origin=origin,
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    queue = deque(script)
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(queue, requests),
    )
    return client, requests


def _request_url(request) -> str:
    scheme = request.url.scheme.decode()
    host = request.url.host.decode()
    target = request.url.target.decode()
    default_port = 443 if scheme == "https" else 80
    authority = (
        host if request.url.port == default_port else f"{host}:{request.url.port}"
    )
    return f"{scheme}://{authority}{target}"


def _headers(request) -> dict[str, str]:
    return {
        name.decode("ascii").lower(): value.decode("latin-1")
        for name, value in request.headers
    }


def test_redirects_are_manual_relative_and_re_evaluated_per_hop(monkeypatch):
    origin = "http://service.test:8080"
    client, requests = _scripted_client(
        monkeypatch,
        "runtime.local_probe",
        origin,
        [
            (302, [(b"location", b"/second")], b""),
            (200, [(b"content-type", b"text/plain")], b"ok"),
        ],
    )

    with client:
        response = client.get(f"{origin}/first")

    assert response.content == b"ok"
    assert [_request_url(request) for request in requests] == [
        f"{origin}/first",
        f"{origin}/second",
    ]
    assert (
        len([event for event in client.audit_events if event.reason == "ALLOWED"]) == 2
    )


def test_real_server_relative_redirect_is_sent_as_two_constrained_requests():
    server = _RedirectServer()
    try:
        origin = f"http://redirect.test:{server.server_address[1]}"
        client = safe_client(
            "runtime.local_probe",
            configured_origin=origin,
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("127.0.0.1",),
                owner_exceptions=(
                    OwnerURLException(
                        consumer="runtime.local_probe",
                        network=ipaddress.ip_network("127.0.0.0/8"),
                    ),
                ),
            ),
        )

        with client:
            response = client.get(f"{origin}/start")

        assert response.content == b"ok"
        assert server.paths == ["/start", "/next"]
    finally:
        server.close()


def test_async_redirects_are_manual_and_re_evaluated_per_hop(monkeypatch):
    async def exercise():
        origin = "http://service.test:8080"
        client = safe_async_client(
            "runtime.local_probe",
            configured_origin=origin,
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("93.184.216.34",)
            ),
        )
        requests = []
        script = deque(
            [
                (302, [(b"location", b"/second")], b""),
                (200, [(b"content-type", b"text/plain")], b"ok"),
            ]
        )
        monkeypatch.setattr(
            client._transport,
            "_pool",
            lambda _decision: _AsyncScriptedPool(script, requests),
        )
        async with client:
            response = await client.get(f"{origin}/first")
        return response, requests, client.audit_events

    response, requests, audit_events = asyncio.run(exercise())
    assert response.content == b"ok"
    assert [_request_url(request) for request in requests] == [
        "http://service.test:8080/first",
        "http://service.test:8080/second",
    ]
    assert len([event for event in audit_events if event.reason == "ALLOWED"]) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_no_redirect_never_contacts_target(asynchronous):
    server = _RedirectServer()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    options = dict(configured_origin=origin, security=OutboundSecurityConfig(
        owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),),
    ))
    async def exercise():
        async with safe_async_client("runtime.local_probe", **options) as client:
            await client.get(origin + "/start", headers={"Authorization": "Bearer test-only"}, follow_redirects=False)
    try:
        with pytest.raises(URLPolicyError, match="REDIRECT_FORBIDDEN"):
            if asynchronous:
                asyncio.run(exercise())
            else:
                with safe_client("runtime.local_probe", **options) as client:
                    client.get(origin + "/start", headers={"Authorization": "Bearer test-only"}, follow_redirects=False)
        assert server.paths == ["/start"]
    finally:
        server.close()


def test_http_to_https_is_allowed_but_https_to_http_is_denied(monkeypatch):
    public_client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    requests = []
    script = deque(
        [
            (302, [(b"location", b"https://public.test/next")], b""),
            (200, [(b"content-type", b"text/plain")], b"ok"),
        ]
    )
    monkeypatch.setattr(
        public_client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(script, requests),
    )
    with public_client:
        assert public_client.get("http://public.test/start").content == b"ok"

    secure_client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    requests = []
    script = deque([(302, [(b"location", b"http://public.test/down")], b"")])
    monkeypatch.setattr(
        secure_client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(script, requests),
    )
    with secure_client, pytest.raises(URLPolicyError) as exc:
        secure_client.get("https://public.test/start")

    assert exc.value.reason == "HTTPS_DOWNGRADE"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Authorization", "Bearer secret-auth"),
        ("Proxy-Authorization", "Basic secret-proxy"),
        ("Cookie", "session=secret-cookie"),
        ("X-API-Key", "secret-api-key"),
    ],
)
def test_configured_credentials_never_reach_cross_origin_redirect(
    monkeypatch, header, value
):
    origin = "https://service.test"
    client, requests = _scripted_client(
        monkeypatch,
        "provider.configured_api",
        origin,
        [(302, [(b"location", b"https://other.test/next")], b"")],
    )

    with client, pytest.raises(URLPolicyError):
        client.get(f"{origin}/start", headers={header: value})

    assert len(requests) == 1
    if header.lower() == "proxy-authorization":
        assert "proxy-authorization" not in _headers(requests[0])
    else:
        assert _headers(requests[0])[header.lower()] == value


def test_explicit_cookie_jar_is_confined_to_the_initial_origin(monkeypatch):
    origin = "https://service.test"
    client, requests = _scripted_client(
        monkeypatch,
        "provider.configured_api",
        origin,
        [(302, [(b"location", b"https://other.test/next")], b"")],
    )
    client.cookies.set("session", "cookie-secret", domain="service.test")

    with client, pytest.raises(URLPolicyError):
        client.get(f"{origin}/start")

    assert _headers(requests[0])["cookie"] == "session=cookie-secret"
    assert len(requests) == 1


def test_url_userinfo_is_rejected_without_a_send_and_audit_is_sanitized(monkeypatch):
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    sent = []
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: sent.append(True),
    )

    with client, pytest.raises(URLPolicyError):
        client.get("https://user:token@public.test/path?secret=query#fragment")

    rendered = repr(client.audit_events)
    assert not sent
    assert client.audit_events[0].reason == "USERINFO_FORBIDDEN"
    assert "token" not in rendered
    assert "query" not in rendered
    assert "fragment" not in rendered
    assert "user" not in rendered


def test_public_consumer_strips_explicit_auth_before_transport(monkeypatch):
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    requests = []
    script = deque([(200, [(b"content-type", b"text/plain")], b"ok")])
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(script, requests),
    )

    with client:
        client.get("https://public.test/resource", auth=("user", "secret"))

    assert "authorization" not in _headers(requests[0])


def test_307_rejects_a_non_rewindable_request_body(monkeypatch):
    origin = "https://service.test"
    client, requests = _scripted_client(
        monkeypatch,
        "provider.configured_api",
        origin,
        [(307, [(b"location", b"/next")], b"")],
    )

    with client, pytest.raises(URLPolicyError) as exc:
        client.post(f"{origin}/start", content=iter([b"body"]))

    assert exc.value.reason == "NON_REWINDABLE_BODY"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status_code", "redirected_method", "keeps_body"),
    [
        (301, b"GET", False),
        (302, b"GET", False),
        (303, b"GET", False),
        (307, b"POST", True),
        (308, b"POST", True),
    ],
)
def test_redirect_method_and_rewindable_body_rules(
    monkeypatch, status_code, redirected_method, keeps_body
):
    origin = "https://service.test"
    client, requests = _scripted_client(
        monkeypatch,
        "provider.configured_api",
        origin,
        [
            (status_code, [(b"location", b"/next")], b""),
            (200, [(b"content-type", b"text/plain")], b"ok"),
        ],
    )

    with client:
        response = client.post(f"{origin}/start", content=b"body")

    redirected_headers = _headers(requests[1])
    redirected_body = b"".join(requests[1].stream)
    assert response.content == b"ok"
    assert [request.method for request in requests] == [b"POST", redirected_method]
    assert (redirected_headers.get("content-length") == "4") is keeps_body
    assert redirected_body == (b"body" if keeps_body else b"")


@pytest.mark.parametrize(
    ("status_code", "redirected_method", "keeps_body"),
    [
        (301, b"GET", False),
        (302, b"GET", False),
        (303, b"GET", False),
        (307, b"POST", True),
        (308, b"POST", True),
    ],
)
def test_async_redirect_method_and_rewindable_body_rules(
    monkeypatch, status_code, redirected_method, keeps_body
):
    async def exercise():
        origin = "https://service.test"
        client = safe_async_client(
            "provider.configured_api",
            configured_origin=origin,
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("93.184.216.34",)
            ),
        )
        requests = []
        script = deque(
            [
                (status_code, [(b"location", b"/next")], b""),
                (200, [(b"content-type", b"text/plain")], b"ok"),
            ]
        )
        monkeypatch.setattr(
            client._transport,
            "_pool",
            lambda _decision: _AsyncScriptedPool(script, requests),
        )
        async with client:
            response = await client.post(f"{origin}/start", content=b"body")
        redirected_body = b"".join([chunk async for chunk in requests[1].stream])
        return response, requests, redirected_body

    response, requests, redirected_body = asyncio.run(exercise())
    redirected_headers = _headers(requests[1])
    assert response.content == b"ok"
    assert [request.method for request in requests] == [b"POST", redirected_method]
    assert (redirected_headers.get("content-length") == "4") is keeps_body
    assert redirected_body == (b"body" if keeps_body else b"")


@pytest.mark.parametrize(
    ("locations", "reason"),
    [
        (["/loop"], "REDIRECT_LOOP"),
        ([f"/hop-{index}" for index in range(6)], "TOO_MANY_REDIRECTS"),
    ],
)
def test_redirect_loop_and_sixth_redirect_are_rejected(monkeypatch, locations, reason):
    origin = "https://service.test"
    if reason == "REDIRECT_LOOP":
        start = f"{origin}/loop"
    else:
        start = f"{origin}/start"
    client, requests = _scripted_client(
        monkeypatch,
        "provider.configured_api",
        origin,
        [(302, [(b"location", location.encode())], b"") for location in locations],
    )

    with client, pytest.raises(URLPolicyError) as exc:
        client.get(start)

    assert exc.value.reason == reason
    assert len(requests) <= 6


def test_private_redirect_is_rejected_before_the_next_send(monkeypatch):
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    requests = []
    script = deque([(302, [(b"location", b"http://127.0.0.1/private")], b"")])
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(script, requests),
    )

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("http://public.test/start")

    assert exc.value.reason == "NON_GLOBAL_ADDRESS"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("location", "reason", "sensitive"),
    [
        ("https://user:secret@other.test/x", "USERINFO_FORBIDDEN", "secret"),
        ("https://other.test/%0aSecret", "CONTROL_CHARACTER", "%0a"),
        ("http://[", "INVALID_URL", "http://["),
    ],
)
def test_rejected_redirect_location_closes_and_audits_safely(
    monkeypatch, location, reason, sensitive
):
    client = safe_client(
        "tool.web_fetch",
        security=OutboundSecurityConfig(
            resolver=lambda _host, _port: ("93.184.216.34",)
        ),
    )
    requests = []
    streams = []
    script = deque([(302, [(b"location", location.encode())], b"")])
    monkeypatch.setattr(
        client._transport,
        "_pool",
        lambda _decision: _ScriptedPool(script, requests, streams),
    )

    with client, pytest.raises(URLPolicyError) as exc:
        client.get("https://public.test/start")

    denials = [event for event in client.audit_events if event.reason != "ALLOWED"]
    assert exc.value.reason == reason
    assert len(requests) == 1
    assert streams[0].closed
    assert [event.reason for event in denials] == [reason]
    assert sensitive not in str(exc.value)
    assert sensitive not in repr(denials)


@pytest.mark.parametrize(
    ("location", "reason", "sensitive"),
    [
        ("https://user:secret@other.test/x", "USERINFO_FORBIDDEN", "secret"),
        ("https://other.test/%0aSecret", "CONTROL_CHARACTER", "%0a"),
        ("http://[", "INVALID_URL", "http://["),
    ],
)
def test_async_rejected_redirect_location_closes_and_audits_safely(
    monkeypatch, location, reason, sensitive
):
    async def exercise():
        client = safe_async_client(
            "tool.web_fetch",
            security=OutboundSecurityConfig(
                resolver=lambda _host, _port: ("93.184.216.34",)
            ),
        )
        requests = []
        streams = []
        script = deque([(302, [(b"location", location.encode())], b"")])
        monkeypatch.setattr(
            client._transport,
            "_pool",
            lambda _decision: _AsyncScriptedPool(script, requests, streams),
        )
        async with client:
            with pytest.raises(URLPolicyError) as exc:
                await client.get("https://public.test/start")
        return exc.value, requests, streams, client.audit_events

    error, requests, streams, audit_events = asyncio.run(exercise())
    denials = [event for event in audit_events if event.reason != "ALLOWED"]
    assert error.reason == reason
    assert len(requests) == 1
    assert streams[0].closed
    assert [event.reason for event in denials] == [reason]
    assert sensitive not in str(error)
    assert sensitive not in repr(denials)
