from __future__ import annotations

import base64
import http.server
import ipaddress
import json
import threading
from dataclasses import replace
from pathlib import Path

import httpcore
import pytest

from openprogram.channels import _attachments, _transport
from openprogram.channels._message import Attachment
from openprogram.functions.tools.image_analyze.providers import gemini
from openprogram.functions.tools.image_generate import image_generate
from openprogram.functions.tools.image_generate.registry import GeneratedImage
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
        body = b"IMGDATA"
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
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


@pytest.fixture
def server():
    instance = _Server()
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
    assert Path(saved[0]["path"]).read_bytes() == b"IMGDATA"
    assert saved[0]["mime"] == "image/png"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/attachment")
    ]
    assert "authorization" not in server.requests[0][2]
    assert "cookie" not in server.requests[0][2]


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
    assert saved_path.read_bytes() == b"IMGDATA"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/generated")
    ]


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


class _GeminiAPIResponse:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "seen"}]}}]}
        ).encode()


def test_gemini_url_image_ingestion_uses_managed_fetch(
    server: _Server, managed_clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gemini.GeminiVisionProvider, "_resolve_key", lambda _self: "k")
    monkeypatch.setattr(
        gemini.urllib.request, "urlopen", lambda *_a, **_k: _GeminiAPIResponse()
    )

    result = gemini.GeminiVisionProvider().analyze(
        [gemini.ImageInput(url=f"http://public.test:{server.port}/vision")],
        "describe",
    )

    assert result == "seen"
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/vision")
    ]


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


class _SlackResponse:
    def __init__(self, data: dict, *, status_code: int = 200):
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers: dict[str, str] = {}
        self.text = json.dumps(data)

    def json(self):
        return self._data


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

    monkeypatch.setattr(_transport.requests, "post", slack_api)
    result = _transport.post_file("slack", "a1", "C1_U1", str(path))

    assert result.ok and result.message_id == "F1"
    assert calls == 2
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("POST", "/upload")
    ]
    assert server.bodies == [b"payload"]
    assert "authorization" not in server.requests[0][2]


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

    monkeypatch.setattr(_transport.requests, "post", slack_api)
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
    assert Path(saved[0]["path"]).read_bytes() == b"IMGDATA"
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
