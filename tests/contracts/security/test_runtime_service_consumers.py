from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import traceback
import http.server
import json
from dataclasses import replace
from types import MappingProxyType

import pytest


class _Response:
    status_code = 200
    content = b"audio"
    text = "ok"
    is_success = True

    def json(self):
        return {"ok": True, "result": {"id": 1, "username": "bot"}}


class _CompatibilityHandler(http.server.BaseHTTPRequestHandler):
    paths: list[str] = []
    hosts: list[str] = []

    def _respond(self):
        self.paths.append(self.path)
        self.hosts.append(self.headers.get("host", ""))
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        if self.path.endswith("/getMe"):
            payload = {"ok": True, "result": {"id": 1, "username": "mapped"}}
        elif self.path.endswith("/count_tokens"):
            payload = {"input_tokens": 3}
        elif self.path.endswith("/sendmessage"):
            payload = {"ret": 0}
        else:
            payload = {"ok": True}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, *_args):
        return


@pytest.fixture
def compatibility_server():
    _CompatibilityHandler.paths = []
    _CompatibilityHandler.hosts = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CompatibilityHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _map_fixed_consumer(monkeypatch, consumer, hostname, port):
    from openprogram.security import safe_http
    from openprogram.security.url_policy import OwnerURLException

    origin = f"http://{hostname}:{port}"
    registry = dict(safe_http.CONSUMER_REGISTRY)
    registry[consumer] = replace(
        registry[consumer],
        trust_class=safe_http.URLTrustClass.CONFIGURED_SERVICE,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        fixed_origins=frozenset(),
        allow_owner_exceptions=True,
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", MappingProxyType(registry))
    original = safe_http.safe_client

    def mapped(mapped_consumer, **kwargs):
        if mapped_consumer == consumer:
            kwargs["configured_origin"] = origin
            kwargs["security"] = safe_http.OutboundSecurityConfig(
                resolver=lambda resolved, _port: (
                    ("127.0.0.1",) if resolved == hostname else ()
                ),
                owner_exceptions=(
                    OwnerURLException(consumer=mapped_consumer, origin=origin),
                ),
            )
        return original(mapped_consumer, **kwargs)

    monkeypatch.setattr(safe_http, "safe_client", mapped)
    return mapped, origin


class _Client:
    def __init__(self, calls, consumer, **kwargs):
        self.calls = calls
        self.consumer = consumer
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def request(self, method, url, **kwargs):
        self.calls.append((self.consumer, method, url, kwargs, self.kwargs))
        return _Response()

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


@pytest.fixture
def managed_service_clients(monkeypatch):
    from openprogram.security import safe_http

    calls = []

    def factory(consumer, **kwargs):
        return _Client(calls, consumer, **kwargs)

    monkeypatch.setattr(safe_http, "safe_client", factory)
    monkeypatch.setattr(
        "requests.get",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("raw requests.get")),
    )
    monkeypatch.setattr(
        "requests.post",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("raw requests.post")),
    )
    return calls


def test_telegram_get_me_uses_fixed_managed_service_client(managed_service_clients):
    from openprogram.channels.implementations.telegram import TelegramChannel

    channel = TelegramChannel.__new__(TelegramChannel)
    channel.base = "https://api.telegram.org/botTOKEN-PATH"
    assert channel._get_me() == {"id": 1, "username": "bot"}
    assert managed_service_clients[0][:3] == (
        "channel.telegram.api",
        "GET",
        "https://api.telegram.org/botTOKEN-PATH/getMe",
    )


@pytest.mark.parametrize(
    ("provider", "config", "consumer"),
    [
        (
            "openai",
            {
                "api_key_env": "TTS_TEST_KEY",
                "base_url": "http://localhost:7777/v1/audio/speech",
            },
            "tts.configured_api",
        ),
        (
            "elevenlabs",
            {"api_key_env": "TTS_TEST_KEY"},
            "tts.fixed_api",
        ),
    ],
)
def test_tts_http_providers_use_declared_managed_service(
    monkeypatch, managed_service_clients, provider, config, consumer
):
    import openprogram.tts as tts

    monkeypatch.setenv("TTS_TEST_KEY", "secret")
    result = (
        tts._openai_tts("hello", config)
        if provider == "openai"
        else tts._elevenlabs_tts("hello", config)
    )
    assert result is not None
    try:
        assert Path(result).read_bytes() == b"audio"
        assert managed_service_clients[0][0] == consumer
    finally:
        Path(result).unlink(missing_ok=True)


def test_mcp_http_and_sse_supply_managed_sdk_clients():
    from openprogram.mcp.client import MCPClient

    for transport in ("http", "sse"):
        instance = MCPClient.__new__(MCPClient)
        instance.config = SimpleNamespace(
            type=transport,
            url="http://localhost:9000/mcp",
            headers={"Authorization": "Bearer secret"},
            timeout_seconds=10.0,
        )
        factory = instance._managed_http_client_factory()
        client = factory(
            headers=instance.config.headers,
            timeout=10.0,
            auth=None,
        )
        try:
            assert client._transport._consumer == f"mcp.configured.{transport}"
            assert client._transport._configured_origin == "http://localhost:9000"
        finally:
            import asyncio

            asyncio.run(client.aclose())


def test_channel_error_mapping_never_echoes_peer_body_or_url():
    from openprogram.channels._transport import (
        _classify_http_status,
        _classify_network_error,
    )

    secret = "token=peer-secret"
    status = _classify_http_status(
        429,
        '{"retry_after": 7, "message": "' + secret + '"}',
    )
    network = _classify_network_error(RuntimeError(f"https://peer.invalid/?{secret}"))

    assert status.error_kind == "rate_limit"
    assert status.retry_after == 7
    assert status.error_detail == "HTTP 429"
    assert network.error_detail == "RuntimeError"
    assert secret not in repr((status, network))


def test_telegram_poll_does_not_echo_malformed_peer_response(monkeypatch, capsys):
    from openprogram.channels.implementations.telegram import TelegramChannel
    from openprogram.security import safe_http

    secret = "peer-secret-query"
    stop = threading.Event()

    class Response:
        status_code = 400
        is_success = False
        text = f"https://api.telegram.org/botTOKEN/getUpdates?leak={secret}"

        def json(self):
            return {"ok": False, "description": secret}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            stop.set()
            return Response()

    channel = TelegramChannel.__new__(TelegramChannel)
    channel.account_id = "test"
    channel.base = "https://api.telegram.org/botTOKEN"
    channel.offset = 0
    monkeypatch.setattr(channel, "_get_me", lambda: None)
    monkeypatch.setattr(safe_http, "safe_client", lambda _consumer: Client())
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    channel.run(stop)

    output = capsys.readouterr().out
    assert "API error 400" in output
    assert secret not in output
    assert "botTOKEN" not in output


def test_attachment_failure_does_not_echo_url_secret(monkeypatch, capsys):
    from openprogram.channels import _attachments
    from openprogram.channels._message import Attachment

    secret = "attachment-secret"

    class RejectingClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            raise RuntimeError(f"https://peer.invalid/file?token={secret}")

    monkeypatch.setattr(
        _attachments, "safe_client", lambda _consumer: RejectingClient()
    )
    assert (
        _attachments.download_inbound(
            "discord",
            "test",
            [Attachment(name="payload.bin", url="https://peer.invalid/file")],
        )
        == []
    )
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert secret not in output


def _render_exception(exc: BaseException) -> str:
    related = [exc, exc.__cause__, exc.__context__]
    return "\n".join(
        [str(item) + repr(item) for item in related if item is not None]
        + traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


class _AsyncRejectingClient:
    def __init__(self, status_code: int, secret: str):
        self.response = SimpleNamespace(
            status_code=status_code,
            is_success=False,
            text=secret,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, *_args, **_kwargs):
        return self.response


def test_device_code_init_failure_never_exposes_peer_envelope(monkeypatch):
    import asyncio
    from openprogram.auth.methods.device_code import DeviceCodeConfig, DeviceCodeMethod
    from openprogram.security import safe_http

    secret = "device_code=peer-secret"
    monkeypatch.setattr(
        safe_http,
        "configured_safe_async_client",
        lambda *_args, **_kwargs: _AsyncRejectingClient(400, secret),
    )
    method = DeviceCodeMethod(
        "test",
        DeviceCodeConfig(
            device_code_url="https://github.com/login/device/code",
            token_url="https://github.com/login/oauth/access_token",
            client_id="client",
        ),
    )

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(method.run(SimpleNamespace()))
    rendered = _render_exception(raised.value)
    assert "device-code init failed: 400" in rendered
    assert secret not in rendered


def test_pkce_token_failure_never_exposes_code_or_peer_envelope(monkeypatch):
    import asyncio
    from openprogram.auth.methods.pkce_oauth import (
        PkceConfig,
        _exchange_code_for_tokens,
    )
    from openprogram.security import safe_http

    secret = "access_token=peer-secret"
    monkeypatch.setattr(
        safe_http,
        "configured_safe_async_client",
        lambda *_args, **_kwargs: _AsyncRejectingClient(401, secret),
    )
    cfg = PkceConfig(
        authorize_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        client_id="client",
    )

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(
            _exchange_code_for_tokens(
                cfg=cfg,
                code="authorization-code-secret",
                verifier="verifier-secret",
                redirect_uri="http://localhost:1455/auth/callback",
            )
        )
    rendered = _render_exception(raised.value)
    assert "token exchange failed: 401" in rendered
    assert secret not in rendered
    assert "authorization-code-secret" not in rendered
    assert "verifier-secret" not in rendered


def test_provider_oauth_public_login_failure_is_sanitized(monkeypatch):
    import asyncio
    from openprogram.providers.utils.oauth import anthropic

    secret = "refresh_token=peer-secret"
    monkeypatch.setattr(
        anthropic,
        "safe_async_client",
        lambda *_args, **_kwargs: _AsyncRejectingClient(403, secret),
    )

    async def prompt_code():
        return "authorization-code#state-secret"

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(anthropic.login_anthropic(lambda _url: None, prompt_code))
    rendered = _render_exception(raised.value)
    assert "Token exchange failed: HTTP 403" in rendered
    assert secret not in rendered
    assert "authorization-code" not in rendered
    assert "state-secret" not in rendered


def test_local_tts_reaches_real_managed_socket(monkeypatch):
    import openprogram.tts as tts

    class Handler(http.server.BaseHTTPRequestHandler):
        paths: list[str] = []

        def do_POST(self):
            self.paths.append(self.path)
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            body = b"managed-audio"
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TTS_LOCAL_KEY", "secret")
    result = None
    try:
        result = tts._openai_tts(
            "hello",
            {
                "api_key_env": "TTS_LOCAL_KEY",
                "base_url": (
                    f"http://127.0.0.1:{server.server_address[1]}/v1/audio/speech"
                ),
            },
        )
        assert result is not None
        assert Path(result).read_bytes() == b"managed-audio"
        assert Handler.paths == ["/v1/audio/speech"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if result:
            Path(result).unlink(missing_ok=True)


def test_telegram_fixed_api_reaches_mapped_real_socket(
    monkeypatch, compatibility_server
):
    from openprogram.channels.implementations.telegram import TelegramChannel

    port = compatibility_server.server_address[1]
    _mapped, origin = _map_fixed_consumer(
        monkeypatch, "channel.telegram.api", "api.telegram.org", port
    )
    channel = TelegramChannel.__new__(TelegramChannel)
    channel.base = origin + "/botTOKEN"

    assert channel._get_me() == {"id": 1, "username": "mapped"}
    assert _CompatibilityHandler.paths == ["/botTOKEN/getMe"]
    assert _CompatibilityHandler.hosts == [f"api.telegram.org:{port}"]


def test_fixed_provider_entry_reaches_mapped_real_socket(
    monkeypatch, compatibility_server
):
    from openprogram.providers._shared import anthropic_token_count

    port = compatibility_server.server_address[1]
    mapped, origin = _map_fixed_consumer(
        monkeypatch, "provider.fixed_api", "api.anthropic.com", port
    )
    monkeypatch.setattr(anthropic_token_count, "safe_client", mapped)
    monkeypatch.setattr(
        anthropic_token_count,
        "_API_URL",
        origin + "/v1/messages/count_tokens",
    )

    result = anthropic_token_count.count_tokens_via_anthropic(
        [{"role": "user", "content": "hello"}],
        "claude-test",
        api_key="secret",
    )
    assert result == {"input_tokens": 3}
    assert _CompatibilityHandler.hosts == [f"api.anthropic.com:{port}"]


def test_wechat_internal_configured_base_reaches_real_socket(
    monkeypatch, compatibility_server
):
    from openprogram.channels import _transport
    from openprogram.channels import accounts

    port = compatibility_server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(
        accounts,
        "load_credentials",
        lambda *_args: {
            "bot_token": "secret",
            "ilink_bot_id": "bot",
            "baseurl": base,
        },
    )

    result = _transport._post_wechat("test", "user", "hello")
    assert result.ok
    assert _CompatibilityHandler.paths == ["/ilink/bot/sendmessage"]


def test_matrix_configured_registry_entry_supports_real_local_service(
    compatibility_server,
):
    from openprogram.security.safe_http import configured_safe_client
    from openprogram.security.url_policy import OwnerURLException

    origin = f"http://127.0.0.1:{compatibility_server.server_address[1]}"
    with configured_safe_client(
        "channel.matrix.configured",
        origin,
        owner_exception=OwnerURLException(
            consumer="channel.matrix.configured", origin=origin
        ),
    ) as client:
        response = client.get(origin + "/_matrix/client/versions")
    assert response.status_code == 200
    assert _CompatibilityHandler.paths == ["/_matrix/client/versions"]


def test_backend_owner_probe_uses_exact_runtime_origin_not_mcp_callback(
    monkeypatch,
):
    from openprogram import _ports
    from openprogram import backend_endpoint
    from openprogram.security import safe_http

    origin = "https://worker.internal.example:9443"
    active = SimpleNamespace(port=9443, effective_origins=(origin,))
    monkeypatch.setattr(backend_endpoint, "read_active_web_access", lambda: active)
    monkeypatch.setattr(backend_endpoint, "read_web_token", lambda: "owner-token")
    monkeypatch.setattr(
        backend_endpoint,
        "create_owner_challenge_proof",
        lambda **_kwargs: "expected-proof",
    )
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"proof": "expected-proof"}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            seen.update(url=url, kwargs=kwargs)
            return Response()

    def configured(consumer, configured_url, *, owner_exception):
        seen.update(
            consumer=consumer,
            configured_url=configured_url,
            owner_exception=owner_exception,
        )
        return Client()

    monkeypatch.setattr(safe_http, "configured_safe_client", configured)

    assert _ports.backend_accepts_owner_challenge(9443, origin=origin)
    assert seen["consumer"] == "runtime.local_probe"
    assert seen["configured_url"] == origin
    assert seen["owner_exception"].consumer == "runtime.local_probe"
    assert seen["owner_exception"].origin == origin
