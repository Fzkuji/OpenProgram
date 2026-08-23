from __future__ import annotations

import base64
import contextlib
import http.server
import ipaddress
import json
import os
import socketserver
import threading
import traceback
from dataclasses import replace
from pathlib import Path

import httpcore
import httpx
import pytest

from openprogram.channels import _attachments, _transport
from openprogram.channels._message import Attachment
from openprogram.programs.tools.web.image_analyze.providers import gemini
from openprogram.programs.tools.web.image_generate import image_generate
from openprogram.programs.tools.web.image_generate.registry import GeneratedImage
from openprogram.security import safe_http
from openprogram.security.safe_http import OutboundSecurityConfig


_REAL_DECISION_BACKEND = safe_http.DecisionNetworkBackend
_PUBLIC_IP = "93.184.216.34"
_CONSUMERS = (
    "channel.attachment.download",
    "channel.slack.attachment",
    "channel.telegram.attachment",
    "channel.slack.generated_asset.upload",
    "tool.image_result.download",
)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.bodies: list[bytes] = []
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

    def handle_error(self, *_args) -> None:
        pass


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        self.server.requests.append(
            (self.command, self.path, {k.lower(): v for k, v in self.headers.items()})
        )

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        if self.path == "/redirect-other":
            server = self.server
            assert isinstance(server, _Server)
            self.send_response(302)
            self.send_header("Location", f"http://other.test:{server.port}/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/private/TOKEN-PATH.png?"):
            body = b"forbidden"
            self.send_response(403)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/html":
            body = b"<html>not an image</html>"
            content_type = "text/html"
        elif self.path == "/fake-image":
            body = b"<html>not an image</html>"
            content_type = "image/png"
        elif self.path == "/octet-webp":
            body = b"RIFF\x04\x00\x00\x00WEBPdata"
            content_type = "application/octet-stream"
        else:
            body = b"\x89PNG\r\n\x1a\nIMGDATA"
            content_type = None if self.path == "/no-mime-png" else "image/png"
        self.send_response(200)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";", 1)[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.extend(self.rfile.read(size))
                self.rfile.read(2)
            self.server.bodies.append(bytes(chunks))
        else:
            length = int(self.headers.get("Content-Length", "0"))
            self.server.bodies.append(self.rfile.read(length) if length else b"")
        if self.path == "/redirect-private":
            server = self.server
            assert isinstance(server, _Server)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{server.port}/private")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
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


class _CountingStream(httpx.SyncByteStream):
    def __init__(self):
        self.chunks_read = 0
        self.closed = False

    def __iter__(self):
        self.chunks_read += 1
        yield b"error body"

    def close(self) -> None:
        self.closed = True


class _CountingClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.response.close()

    def get(self, *_args, **_kwargs):
        self.response.read()
        return self.response

    @contextlib.contextmanager
    def stream(self, *_args, **_kwargs):
        try:
            yield self.response
        finally:
            self.response.close()


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
def managed_clients(monkeypatch: pytest.MonkeyPatch, server: _Server):
    registry = dict(safe_http.CONSUMER_REGISTRY)
    for consumer in _CONSUMERS:
        spec = registry[consumer]
        changes = {"allowed_ports": frozenset({server.port})}
        if spec.fixed_origins:
            changes.update(
                allowed_schemes=frozenset({"http"}),
                fixed_origins=frozenset({f"http://public.test:{server.port}"}),
            )
        registry[consumer] = replace(spec, **changes)
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
        return (str(ipaddress.ip_address(hostname)),)

    clients = []

    def factory(consumer: str):
        client = safe_http.safe_client(
            consumer, security=OutboundSecurityConfig(resolver=resolver)
        )
        clients.append(client)
        return client

    for module in (_attachments, _transport, image_generate, gemini):
        monkeypatch.setattr(module, "safe_client", factory, raising=False)
    return clients


@pytest.fixture(autouse=True)
def _write_inside_sandbox_roots(request, monkeypatch: pytest.MonkeyPatch) -> None:
    if "tmp_path" in request.fixturenames:
        monkeypatch.chdir(request.getfixturevalue("tmp_path"))


def test_channel_attachment_download_uses_managed_public_fetch(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="pic.png",
                mime="image/png",
                url=f"http://public.test:{server.port}/attachment",
                headers=(
                    ("Authorization", "Bearer SECRET"),
                    ("Cookie", "session=SECRET"),
                ),
            )
        ],
    )

    assert len(saved) == 1
    assert Path(saved[0]["path"]).read_bytes() == b"\x89PNG\r\n\x1a\nIMGDATA"
    assert saved[0]["mime"] == "image/png"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/attachment")
    ]
    assert "authorization" not in server.requests[0][2]
    assert "cookie" not in server.requests[0][2]


def test_attachment_malformed_status_hides_peer_echoed_signed_url(
    malformed_server: _MalformedServer,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["channel.attachment.download"]
    registry[spec.consumer] = replace(
        spec, allowed_ports=frozenset({malformed_server.port})
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")

    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="private.png",
                url=(
                    f"http://public.test:{malformed_server.port}/TOKEN-PATH.png"
                    "?sig=QUERY-SECRET"
                ),
            )
        ],
    )

    output = capsys.readouterr().out
    assert saved == []
    assert output.endswith(
        "download failed: RemoteProtocolError for "
        f"http://public.test:{malformed_server.port}\n"
    )
    assert "TOKEN-PATH" not in output
    assert "QUERY-SECRET" not in output
    assert "TOKEN-PATH" not in repr(managed_clients[-1].audit_events)
    assert "QUERY-SECRET" not in repr(managed_clients[-1].audit_events)


def test_channel_attachment_private_dns_is_denied_before_request(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="pic.png",
                url=f"http://127.0.0.1:{server.port}/private",
            )
        ],
    )

    assert saved == []
    assert server.requests == []


class _ImageBackend:
    name = "fake"

    def __init__(self, url: str):
        self.url = url

    def generate(self, *_args, **_kwargs):
        return [GeneratedImage(url=self.url, mime="image/png")]


def test_image_generation_result_url_uses_managed_fetch(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generate.registry,
        "select",
        lambda **_kwargs: _ImageBackend(f"http://public.test:{server.port}/generated"),
    )
    result = image_generate.execute("draw", output_dir=str(tmp_path))

    assert result.startswith("# image_generate (via fake, 1 image)")
    saved_path = Path(result.splitlines()[-1].removeprefix("- "))
    assert saved_path.read_bytes() == b"\x89PNG\r\n\x1a\nIMGDATA"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/generated")
    ]


@pytest.mark.parametrize(
    ("body", "mime", "extension"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png", ".png"),
        (b"\xff\xd8\xffrest", "image/jpeg", ".jpg"),
        (b"GIF89arest", "image/gif", ".gif"),
        (b"RIFF\x04\x00\x00\x00WEBPrest", "image/webp", ".webp"),
        (b"BMrest", "image/bmp", ".bmp"),
    ],
)
def test_image_generation_validates_supported_raster_magic(
    tmp_path: Path, body: bytes, mime: str, extension: str
) -> None:
    saved = image_generate._save(
        GeneratedImage(data=body, mime="application/octet-stream"),
        tmp_path,
        "image",
        1,
    )

    assert saved.suffix == extension
    assert saved.read_bytes() == body


def test_image_generation_rejects_non_image_bytes_without_replacing_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "image_1.png"
    target.write_bytes(b"existing image")

    with pytest.raises(RuntimeError, match="unsupported raster image bytes"):
        image_generate._save(
            GeneratedImage(data=b"<html>not an image</html>", mime="image/png"),
            tmp_path,
            "image",
            1,
        )

    assert target.read_bytes() == b"existing image"


def test_image_generation_validates_download_magic_before_replacing_target(
    server: _Server,
    managed_clients,
    tmp_path: Path,
) -> None:
    target = tmp_path / "image_1.png"
    target.write_bytes(b"existing image")

    with pytest.raises(RuntimeError, match="unsupported raster image bytes"):
        image_generate._save(
            GeneratedImage(
                url=f"http://public.test:{server.port}/fake-image",
                mime="image/png",
            ),
            tmp_path,
            "image",
            1,
        )

    assert target.read_bytes() == b"existing image"
    assert list(tmp_path.iterdir()) == [target]


def test_image_generation_accepts_octet_stream_using_real_webp_magic(
    server: _Server,
    managed_clients,
    tmp_path: Path,
) -> None:
    saved = image_generate._save(
        GeneratedImage(
            url=f"http://public.test:{server.port}/octet-webp",
            mime="application/octet-stream",
        ),
        tmp_path,
        "image",
        1,
    )

    assert saved.suffix == ".webp"
    assert saved.read_bytes().startswith(b"RIFF")


def test_image_generation_rejects_explicit_html_before_body_persistence(
    server: _Server,
    managed_clients,
    tmp_path: Path,
) -> None:
    with pytest.raises(Exception, match="MIME_TYPE_FORBIDDEN"):
        image_generate._save(
            GeneratedImage(
                url=f"http://public.test:{server.port}/html",
                mime="image/png",
            ),
            tmp_path,
            "image",
            1,
        )

    assert list(tmp_path.iterdir()) == []


def test_image_generation_private_result_is_denied_before_request(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generate.registry,
        "select",
        lambda **_kwargs: _ImageBackend(f"http://127.0.0.1:{server.port}/private"),
    )
    result = image_generate.execute("draw", output_dir=str(tmp_path))

    assert result.startswith("Error: fake save failed at image 1:")
    assert "NON_GLOBAL_ADDRESS" in result
    assert server.requests == []


def test_image_generation_http_error_hides_signed_path_query_and_cause(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generate.registry,
        "select",
        lambda **_kwargs: _ImageBackend(
            f"http://public.test:{server.port}/private/TOKEN-PATH.png?sig=QUERY-SECRET"
        ),
    )

    result = image_generate.execute("draw", output_dir=str(tmp_path))

    assert "HTTP 403 Forbidden" in result
    assert f"http://public.test:{server.port}" in result
    assert "TOKEN-PATH" not in result
    assert "QUERY-SECRET" not in result
    assert "TOKEN-PATH" not in repr(managed_clients[-1].audit_events)
    assert "QUERY-SECRET" not in repr(managed_clients[-1].audit_events)


def test_image_result_http_error_traceback_does_not_retain_signed_url(
    server: _Server, managed_clients, tmp_path: Path
) -> None:
    signed_url = (
        f"http://public.test:{server.port}/private/TOKEN-PATH.png?sig=QUERY-SECRET"
    )

    with pytest.raises(RuntimeError) as caught:
        image_generate._save(
            GeneratedImage(url=signed_url, mime="image/png"),
            tmp_path,
            "image",
            1,
        )

    error = caught.value
    rendered = "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


def test_gemini_url_image_ingestion_uses_managed_fetch(
    server: _Server, managed_clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gemini.GeminiVisionProvider, "_resolve_key", lambda _self: "k")
    monkeypatch.setattr(
        gemini,
        "post_json",
        lambda *_a, **_k: {"candidates": [{"content": {"parts": [{"text": "seen"}]}}]},
    )

    result = gemini.GeminiVisionProvider().analyze(
        [gemini.ImageInput(url=f"http://public.test:{server.port}/vision")],
        "describe",
    )

    assert result == "seen"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/vision")
    ]


@pytest.mark.parametrize(
    ("path", "expected_mime"),
    [("/no-mime-png", "image/png"), ("/octet-webp", "image/webp")],
)
def test_gemini_uses_raster_magic_for_compatible_ambiguous_mime(
    server: _Server, managed_clients, path: str, expected_mime: str
) -> None:
    encoded, mime = gemini._url_to_b64(f"http://public.test:{server.port}{path}")

    assert mime == expected_mime
    assert base64.b64decode(encoded)


@pytest.mark.parametrize("path", ["/html", "/fake-image"])
def test_gemini_never_base64_submits_non_image_content(
    server: _Server, managed_clients, path: str
) -> None:
    with pytest.raises(Exception):
        gemini._url_to_b64(f"http://public.test:{server.port}{path}")


@pytest.mark.parametrize("consumer", ["attachment", "gemini"])
def test_derived_download_checks_4xx_before_consuming_body_and_closes(
    consumer: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stream = _CountingStream()
    response = httpx.Response(
        403,
        headers={"Content-Type": "image/png"},
        stream=stream,
        request=httpx.Request("GET", "https://public.test/private.png"),
    )
    factory = lambda *_args, **_kwargs: _CountingClient(response)

    if consumer == "attachment":
        monkeypatch.setattr(_attachments, "safe_client", factory)
        monkeypatch.setattr(
            "openprogram.paths.get_state_dir", lambda: tmp_path / "state"
        )
        result = _attachments.download_inbound(
            "discord",
            "a1",
            [Attachment(name="private.png", url="https://public.test/private.png")],
        )
        assert result == []
        assert "download HTTP 403" in capsys.readouterr().out
    else:
        monkeypatch.setattr(gemini, "safe_client", factory)
        with pytest.raises(RuntimeError, match="HTTP 403"):
            gemini._url_to_b64("https://public.test/private.png")

    assert stream.chunks_read == 0
    assert stream.closed


def test_gemini_private_url_image_is_denied_before_request(
    server: _Server, managed_clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gemini.GeminiVisionProvider, "_resolve_key", lambda _self: "k")

    with pytest.raises(Exception, match="NON_GLOBAL_ADDRESS"):
        gemini.GeminiVisionProvider().analyze(
            [gemini.ImageInput(url=f"http://127.0.0.1:{server.port}/vision")],
            "describe",
        )
    assert server.requests == []


def test_gemini_http_error_hides_signed_path_query_and_cause(
    server: _Server, managed_clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gemini.GeminiVisionProvider, "_resolve_key", lambda _self: "k")
    signed_url = (
        f"http://public.test:{server.port}/private/TOKEN-PATH.png?sig=QUERY-SECRET"
    )

    with pytest.raises(RuntimeError) as caught:
        gemini.GeminiVisionProvider().analyze(
            [gemini.ImageInput(url=signed_url)], "describe"
        )

    error = caught.value
    rendered = "".join(traceback.format_exception(error))
    assert str(error) == (
        f"Gemini image download HTTP 403 Forbidden for http://public.test:{server.port}"
    )
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "TOKEN-PATH" not in repr(managed_clients[-1].audit_events)
    assert "QUERY-SECRET" not in repr(managed_clients[-1].audit_events)


class _SlackResponse:
    def __init__(self, data: dict, *, status_code: int = 200):
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.is_success = self.ok
        self.headers: dict[str, str] = {}
        self.text = json.dumps(data)

    def json(self):
        return self._data


def _patch_slack_api_client(monkeypatch, post):
    managed_factory = _transport.safe_client

    class SlackClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            return post(url, **kwargs)

    monkeypatch.setattr(
        _transport,
        "safe_client",
        lambda consumer: (
            SlackClient()
            if consumer == "channel.slack.api"
            else managed_factory(consumer)
        ),
    )


def test_channel_generated_upload_url_uses_managed_post(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "asset.bin"
    path.write_bytes(b"payload")
    monkeypatch.setattr(
        _transport._accounts,
        "load_credentials",
        lambda *_args: {"bot_token": "TOKEN"},
    )

    calls = 0

    def slack_api(url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if url.endswith("files.getUploadURLExternal"):
            return _SlackResponse(
                {
                    "ok": True,
                    "upload_url": f"http://public.test:{server.port}/upload",
                    "file_id": "F1",
                }
            )
        if url.endswith("files.completeUploadExternal"):
            return _SlackResponse({"ok": True})
        raise AssertionError(f"raw derived upload attempted: {url}")

    _patch_slack_api_client(monkeypatch, slack_api)
    result = _transport.post_file("slack", "a1", "C1_U1", str(path))

    assert result.ok and result.message_id == "F1"
    assert calls == 2
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("POST", "/upload")
    ]
    assert server.bodies == [b"payload"]
    assert "authorization" not in server.requests[0][2]


def test_slack_upload_malformed_status_hides_peer_echoed_signed_url(
    malformed_server: _MalformedServer,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["channel.slack.generated_asset.upload"]
    registry[spec.consumer] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({malformed_server.port}),
        fixed_origins=frozenset({f"http://public.test:{malformed_server.port}"}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    path = tmp_path / "asset.bin"
    path.write_bytes(b"payload")
    monkeypatch.setattr(
        _transport._accounts,
        "load_credentials",
        lambda *_args: {"bot_token": "TOKEN"},
    )

    def slack_api(url: str, **_kwargs):
        if url.endswith("files.getUploadURLExternal"):
            return _SlackResponse(
                {
                    "ok": True,
                    "upload_url": (
                        f"http://public.test:{malformed_server.port}/TOKEN-PATH"
                        "?sig=QUERY-SECRET"
                    ),
                    "file_id": "F1",
                }
            )
        raise AssertionError(f"unexpected raw request: {url}")

    _patch_slack_api_client(monkeypatch, slack_api)
    result = _transport.post_file("slack", "a1", "C1_U1", str(path))

    assert not result.ok
    assert result.error_detail == (
        f"RemoteProtocolError for http://public.test:{malformed_server.port}"
    )
    rendered = repr(result)
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "TOKEN-PATH" not in repr(managed_clients[-1].audit_events)
    assert "QUERY-SECRET" not in repr(managed_clients[-1].audit_events)


def test_channel_generated_upload_redirect_to_private_is_denied_before_send(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "asset.bin"
    path.write_bytes(b"payload")
    monkeypatch.setattr(
        _transport._accounts,
        "load_credentials",
        lambda *_args: {"bot_token": "TOKEN"},
    )

    def slack_api(url: str, **_kwargs):
        if url.endswith("files.getUploadURLExternal"):
            return _SlackResponse(
                {
                    "ok": True,
                    "upload_url": f"http://public.test:{server.port}/redirect-private",
                    "file_id": "F1",
                }
            )
        raise AssertionError(f"unexpected raw request: {url}")

    _patch_slack_api_client(monkeypatch, slack_api)
    result = _transport.post_file("slack", "a1", "C1_U1", str(path))

    assert not result.ok and result.error_kind == "network"
    assert "REDIRECT_ORIGIN_FORBIDDEN" in result.error_detail
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("POST", "/redirect-private")
    ]


def test_registry_classifies_credential_bearing_channel_urls_exactly() -> None:
    slack_attachment = safe_http.CONSUMER_REGISTRY["channel.slack.attachment"]
    assert slack_attachment.fixed_origins == frozenset(
        {"https://files.slack.com", "https://slack.com"}
    )
    assert slack_attachment.credential_origin_policy == "same_origin"
    assert slack_attachment.allowed_methods == frozenset({"GET", "HEAD"})

    telegram_attachment = safe_http.CONSUMER_REGISTRY["channel.telegram.attachment"]
    assert telegram_attachment.fixed_origins == frozenset({"https://api.telegram.org"})
    assert telegram_attachment.accepted_mime_prefixes == (
        "application/",
        "audio/",
        "image/",
        "text/",
        "video/",
    )

    slack_upload = safe_http.CONSUMER_REGISTRY["channel.slack.generated_asset.upload"]
    assert slack_upload.fixed_origins == frozenset({"https://files.slack.com"})
    assert slack_upload.allowed_methods == frozenset({"POST"})
    assert slack_upload.credential_origin_policy == "none"


def test_slack_private_attachment_keeps_bearer_only_on_fixed_origin(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "slack",
        "a1",
        [
            Attachment(
                name="private.png",
                mime="image/png",
                url=f"http://public.test:{server.port}/private-slack",
                headers=(("Authorization", "Bearer SECRET"),),
            )
        ],
    )

    assert len(saved) == 1
    assert server.requests[0][2]["authorization"] == "Bearer SECRET"


def test_slack_private_attachment_does_not_redirect_bearer_cross_origin(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "slack",
        "a1",
        [
            Attachment(
                name="private.png",
                url=f"http://public.test:{server.port}/redirect-other",
                headers=(("Authorization", "Bearer SECRET"),),
            )
        ],
    )

    assert saved == []
    assert len(server.requests) == 1
    assert server.requests[0][2]["authorization"] == "Bearer SECRET"
    assert managed_clients[-1].audit_events[-1].reason == "REDIRECT_ORIGIN_FORBIDDEN"
    assert "SECRET" not in repr(managed_clients[-1].audit_events)


def test_telegram_fixed_attachment_accepts_image_without_leaking_token_path(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "telegram",
        "a1",
        [
            Attachment(
                name="photo.png",
                mime="image/png",
                url=f"http://public.test:{server.port}/file/botSECRET/photo.png",
            )
        ],
    )

    assert len(saved) == 1
    assert Path(saved[0]["path"]).read_bytes() == b"\x89PNG\r\n\x1a\nIMGDATA"
    assert "SECRET" not in repr(managed_clients[-1].audit_events)


def test_telegram_token_path_is_not_exposed_by_fixed_origin_denial(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    saved = _attachments.download_inbound(
        "telegram",
        "a1",
        [
            Attachment(
                name="photo.png",
                mime="image/png",
                url=f"http://127.0.0.1:{server.port}/file/botSECRET/photo.png",
            )
        ],
    )

    assert saved == []
    output = capsys.readouterr().out
    assert "FIXED_ORIGIN_MISMATCH" in output
    assert "SECRET" not in output
    assert server.requests == []


def test_attachment_disk_failure_removes_partial_destination(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    real_fdopen = _attachments.os.fdopen

    class _PartialFile:
        def __init__(self, descriptor: int, *args, **kwargs):
            self.file = real_fdopen(descriptor, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.file.close()

        def write(self, data: bytes) -> int:
            self.file.write(data[:2])
            raise OSError("disk full")

        def flush(self) -> None:
            self.file.flush()

        def fileno(self) -> int:
            return self.file.fileno()

    monkeypatch.setattr(_attachments.os, "fdopen", _PartialFile)
    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="pic.png",
                url=f"http://public.test:{server.port}/attachment",
            )
        ],
    )

    assert saved == []
    assert list(_attachments.attachments_dir("discord", "a1").iterdir()) == []


def test_attachment_fdopen_failure_closes_descriptor_and_removes_temp(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    descriptors: list[int] = []
    real_mkstemp = _attachments.tempfile.mkstemp

    def record_mkstemp(*args, **kwargs):
        descriptor, name = real_mkstemp(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor, name

    monkeypatch.setattr(_attachments.tempfile, "mkstemp", record_mkstemp)
    monkeypatch.setattr(
        _attachments.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fdopen failed")),
    )

    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="pic.png",
                url=f"http://public.test:{server.port}/attachment",
            )
        ],
    )

    assert saved == []
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(_attachments.attachments_dir("discord", "a1").iterdir()) == []


def test_attachment_atomic_write_fsyncs_before_replace(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    synced: list[int] = []
    real_replace = _attachments.os.replace
    monkeypatch.setattr(_attachments.os, "fsync", lambda fd: synced.append(fd))

    def checked_replace(source, destination):
        assert synced
        return real_replace(source, destination)

    monkeypatch.setattr(_attachments.os, "replace", checked_replace)
    saved = _attachments.download_inbound(
        "discord",
        "a1",
        [
            Attachment(
                name="pic.png",
                url=f"http://public.test:{server.port}/attachment",
            )
        ],
    )

    assert len(saved) == 1
    assert synced


def test_image_result_atomic_download_cleans_temporary_on_replace_failure(
    server: _Server,
    managed_clients,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generate.registry,
        "select",
        lambda **_kwargs: _ImageBackend(f"http://public.test:{server.port}/generated"),
    )
    monkeypatch.setattr(
        safe_http.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = image_generate.execute("draw", output_dir=str(tmp_path))

    assert result.startswith("Error: fake save failed at image 1:")
    assert list(tmp_path.iterdir()) == []
