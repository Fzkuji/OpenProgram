from __future__ import annotations

import inspect
import importlib.metadata
import json
import multiprocessing
import socket
import socketserver
import threading
import time
import traceback
import warnings
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from openprogram.security.safe_http import (
    AsyncDecisionNetworkBackend,
    AsyncManagedHTTPTransport,
    CONSUMER_REGISTRY,
    ManagedHTTPTransport,
    SDKDisposition,
    SafeClient,
    SafeAsyncClient,
)
from openprogram.security.url_policy import (
    OwnerURLException,
    URLPolicyError,
    normalize_origin,
)


class _SDKHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request_line = self.rfile.readline().decode("latin-1")
        headers = {}
        while True:
            line = self.rfile.readline()
            if line in {b"", b"\r\n"}:
                break
            name, value = line.decode("latin-1").split(":", 1)
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length:
            self.rfile.read(length)
        path = request_line.split(" ", 2)[1]
        self.server.paths.append(path)
        self.server.headers.append(headers)
        if self.server.redirect_to:
            self.wfile.write(
                b"HTTP/1.1 302 Found\r\n"
                + f"Location: {self.server.redirect_to}\r\n".encode()
                + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            return
        if "generativelanguage" in headers.get("host", "") or "pageSize" in path:
            payload = {"models": []}
        elif path.rstrip("/").endswith("/models"):
            payload = {"object": "list", "data": [], "has_more": False}
        else:
            payload = {"data": []}
        body = json.dumps(payload).encode()
        self.wfile.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )


class _SDKServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.paths = []
        self.headers = []
        self.redirect_to = None
        super().__init__(("127.0.0.1", 0), _SDKHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server_address[1]

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def sdk_server():
    server = _SDKServer()
    try:
        yield server
    finally:
        server.close()


@pytest.mark.parametrize(
    ("consumer", "disposition"),
    [
        ("provider.openai.sdk", SDKDisposition.INJECTED_TRANSPORT),
        ("provider.anthropic.sdk", SDKDisposition.INJECTED_TRANSPORT),
        ("provider.amazon_bedrock.sdk", SDKDisposition.DISABLED),
        ("provider.google.sdk", SDKDisposition.INJECTED_TRANSPORT),
        ("mcp.configured.http", SDKDisposition.INJECTED_TRANSPORT),
        ("mcp.configured.sse", SDKDisposition.INJECTED_TRANSPORT),
        ("channel.telegram.api", SDKDisposition.EXACT_ORIGIN),
        ("channel.wechat.api", SDKDisposition.EXACT_ORIGIN),
        ("channel.feishu.api", SDKDisposition.EXACT_ORIGIN),
        ("channel.matrix.configured", SDKDisposition.EXACT_ORIGIN),
        ("channel.slack.api", SDKDisposition.EXACT_ORIGIN),
        ("channel.discord.api", SDKDisposition.EXACT_ORIGIN),
        ("channel.slack.gateway_sdk", SDKDisposition.DISABLED),
        ("channel.discord.gateway_sdk", SDKDisposition.DISABLED),
        ("tts.fixed_api", SDKDisposition.EXACT_ORIGIN),
        ("tts.configured_api", SDKDisposition.EXACT_ORIGIN),
        ("tts.edge_sdk", SDKDisposition.DISABLED),
    ],
)
def test_every_task6_sdk_entry_has_an_explicit_disposition(consumer, disposition):
    assert CONSUMER_REGISTRY[consumer].sdk_disposition is disposition


def test_provider_client_factory_requires_policy_scope_and_returns_managed_client():
    from openprogram.providers.utils.http_client import build_async_client

    signature = inspect.signature(build_async_client)
    assert signature.parameters["consumer"].default is inspect.Parameter.empty
    assert signature.parameters["configured_origin"].default is inspect.Parameter.empty

    client = build_async_client(
        consumer="provider.openai.sdk",
        configured_origin="https://api.openai.com",
    )
    try:
        assert type(client) is SafeAsyncClient
        assert type(client._transport) is AsyncManagedHTTPTransport
    finally:
        import asyncio

        asyncio.run(client.aclose())


def test_provider_client_factory_preserves_stream_timeout_and_socket_hardening(
    monkeypatch,
):
    import asyncio
    import socket
    from openprogram.providers.utils import timeouts
    from openprogram.providers.utils.http_client import build_async_client

    monkeypatch.setenv("OPENPROGRAM_FORCE_IPV4", "1")
    client = build_async_client(
        consumer="provider.openai.sdk",
        configured_origin="https://api.openai.com",
    )
    try:
        assert client.timeout.read == timeouts.httpx_read_timeout_s()
        assert client._overall_timeout == timeouts.STREAM_TOTAL_TIMEOUT_S
        security = client._transport._security
        assert security.local_address == "0.0.0.0"
        assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in security.socket_options
    finally:
        asyncio.run(client.aclose())


def test_provider_client_factory_rejects_unmanaged_client_kwargs():
    from openprogram.providers.utils.http_client import build_async_client

    with pytest.raises(TypeError):
        build_async_client(
            consumer="provider.openai.sdk",
            configured_origin="https://api.openai.com",
            mounts={"all://": object()},
        )


def test_real_openai_provider_constructor_receives_managed_httpx_client():
    from openprogram.providers.openai_responses.openai_responses import _create_client
    from openprogram.providers.types import Context, Model

    model = Model(
        id="test",
        name="test",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    client = _create_client(model, Context(messages=[]), "secret")
    try:
        assert type(client._client) is SafeAsyncClient
        assert type(client._client._transport) is AsyncManagedHTTPTransport
        assert client._client._transport._consumer == "provider.openai.sdk"
    finally:
        import asyncio

        asyncio.run(client._client.aclose())


def test_real_anthropic_provider_constructor_receives_managed_httpx_client():
    from openprogram.providers.anthropic.anthropic import _build_client
    from openprogram.providers.types import Model

    model = Model(
        id="claude-test",
        name="test",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
    )
    client, _ = _build_client(model, "secret")
    try:
        assert type(client._client) is SafeAsyncClient
        assert type(client._client._transport) is AsyncManagedHTTPTransport
        assert client._client._transport._consumer == "provider.anthropic.sdk"
    finally:
        import asyncio

        asyncio.run(client._client.aclose())


def test_real_google_sdk_constructor_receives_both_managed_httpx_clients():
    from google import genai
    from openprogram.providers.utils.http_client import build_google_http_options

    options = build_google_http_options("https://generativelanguage.googleapis.com")
    client = genai.Client(api_key="secret", http_options=options)
    try:
        stored = client._api_client._http_options
        assert type(stored.httpx_client) is SafeClient
        assert type(stored.httpx_client._transport) is ManagedHTTPTransport
        assert type(stored.httpx_async_client) is SafeAsyncClient
        assert type(stored.httpx_async_client._transport) is AsyncManagedHTTPTransport
    finally:
        client.close()


def test_real_openai_sdk_method_reaches_managed_socket(sdk_server):
    import asyncio
    from openprogram.providers.openai_responses.openai_responses import _create_client
    from openprogram.providers.types import Context, Model

    base = f"http://127.0.0.1:{sdk_server.port}/v1"
    model = Model(
        id="test", name="test", api="openai-responses", provider="openai", base_url=base
    )
    client = _create_client(model, Context(messages=[]), "secret")

    async def exercise():
        await client.models.list()
        await client._client.aclose()

    asyncio.run(exercise())
    assert sdk_server.paths == ["/v1/models"]
    assert type(client._client._transport) is AsyncManagedHTTPTransport


def test_real_anthropic_sdk_method_reaches_managed_socket(sdk_server):
    import asyncio
    from openprogram.providers.anthropic.anthropic import _build_client
    from openprogram.providers.types import Model

    base = f"http://127.0.0.1:{sdk_server.port}"
    model = Model(
        id="test",
        name="test",
        api="anthropic-messages",
        provider="anthropic",
        base_url=base,
    )
    client, _ = _build_client(model, "secret")

    async def exercise():
        await client.models.list()
        await client._client.aclose()

    asyncio.run(exercise())
    assert sdk_server.paths == ["/v1/models"]
    assert type(client._client._transport) is AsyncManagedHTTPTransport


def test_real_google_sdk_method_reaches_managed_socket(sdk_server):
    from google import genai
    from openprogram.providers.utils.http_client import build_google_http_options

    base = f"http://127.0.0.1:{sdk_server.port}"
    client = genai.Client(
        api_key="secret",
        http_options=build_google_http_options(
            base,
            owner_exception=OwnerURLException(
                consumer="provider.google.sdk", origin=normalize_origin(base)
            ),
        ),
    )
    try:
        list(client.models.list(config={"page_size": 1}))
    finally:
        client.close()
    assert sdk_server.paths
    assert type(client._api_client._http_options.httpx_client) is SafeClient


def test_anthropic_rejects_when_managed_client_factory_rejects(monkeypatch):
    from openprogram.providers.anthropic.anthropic import _build_client
    from openprogram.providers.types import Model
    from openprogram.providers.utils import http_client

    rejection = URLPolicyError("PRIVATE_ADDRESS", "https://private.invalid")

    def reject(*_args, **_kwargs):
        raise rejection

    monkeypatch.setattr(http_client, "get_shared_async_client", reject)
    model = Model(
        id="claude-test",
        name="test",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://private.invalid",
    )

    with pytest.raises(URLPolicyError) as raised:
        _build_client(model, "secret")
    assert raised.value is rejection


def test_real_openai_sdk_refuses_cross_origin_credential_redirect(sdk_server):
    import asyncio
    from openprogram.providers.openai_responses.openai_responses import _create_client
    from openprogram.providers.types import Context, Model

    redirect_target = _SDKServer()
    sdk_server.redirect_to = (
        f"http://127.0.0.1:{redirect_target.port}/credential-target"
    )
    base = f"http://127.0.0.1:{sdk_server.port}/v1"
    model = Model(
        id="test",
        name="test",
        api="openai-responses",
        provider="openai",
        base_url=base,
    )
    client = _create_client(model, Context(messages=[]), "redirect-secret")

    async def exercise():
        try:
            await client.models.list()
        finally:
            await client._client.aclose()

    try:
        with pytest.raises(Exception):
            asyncio.run(exercise())
        assert sdk_server.paths
        assert redirect_target.paths == []
        assert redirect_target.headers == []
    finally:
        redirect_target.close()


def _run_mcp_server(port: int, transport: str):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("managed-test", host="127.0.0.1", port=port)

    @server.tool()
    def ping() -> str:
        return "pong"

    server.run(transport="streamable-http" if transport == "http" else "sse")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(
    int(importlib.metadata.version("mcp").split(".", 1)[0]) >= 2,
    reason="BASE frozen lock installs unsupported mcp 2; run with --with 'mcp<2'",
)
@pytest.mark.parametrize(("transport", "path"), [("http", "/mcp"), ("sse", "/sse")])
def test_real_mcp_v1_sdk_transport_reaches_managed_local_server(
    monkeypatch, transport, path
):
    import asyncio
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import MCPServerConfig

    port = _free_port()
    process = multiprocessing.Process(
        target=_run_mcp_server, args=(port, transport), daemon=True
    )
    process.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        process.terminate()
        pytest.fail("MCP test server did not start")

    async def exercise():
        client = MCPClient(
            MCPServerConfig(
                name=f"managed-{transport}",
                type=transport,
                url=f"http://127.0.0.1:{port}{path}",
                enabled=True,
                timeout_seconds=5,
            )
        )
        factory = client._managed_http_client_factory()
        created = []

        def recording_factory(*args, **kwargs):
            managed = factory(*args, **kwargs)
            created.append(managed)
            return managed

        monkeypatch.setattr(
            client, "_managed_http_client_factory", lambda: recording_factory
        )
        await client.start()
        try:
            assert client.is_ready
            assert created
            assert all(type(item) is SafeAsyncClient for item in created)
            assert all(
                type(item._transport) is AsyncManagedHTTPTransport for item in created
            )
            assert [tool.name for tool in client.tools] == ["ping"]
            result = await client.call_tool("ping", {})
            assert result.content[0].text == "pong"
        finally:
            await client.stop()

    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            asyncio.run(exercise())
        assert not [
            item for item in captured if issubclass(item.category, DeprecationWarning)
        ]
    finally:
        process.terminate()
        process.join(timeout=3)


def test_mcp_older_v1_streamable_http_fallback_keeps_managed_factory(
    monkeypatch,
):
    import asyncio
    import openprogram.mcp.client as client_module
    import mcp.client.streamable_http as streamable_module
    from openprogram.mcp.client import MCPClient

    instance = MCPClient.__new__(MCPClient)
    instance.config = SimpleNamespace(url="https://mcp.example/rpc", timeout_seconds=7)
    managed_factory = object()
    seen = {}

    async def build_auth():
        return {"Authorization": "Bearer secret"}, "auth-object"

    @asynccontextmanager
    async def legacy(url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        yield "read", "write", lambda: None

    async def run_session(read, write):
        seen.update(read=read, write=write)

    instance._build_remote_auth = build_auth
    instance._managed_http_client_factory = lambda: managed_factory
    instance._run_session = run_session
    monkeypatch.setattr(client_module, "_modern_streamable_http_client", None)
    monkeypatch.setattr(streamable_module, "streamablehttp_client", legacy)

    asyncio.run(instance._run_http())

    assert seen["url"] == "https://mcp.example/rpc"
    assert seen["kwargs"]["httpx_client_factory"] is managed_factory
    assert seen["kwargs"]["headers"] == {"Authorization": "Bearer secret"}
    assert seen["read"] == "read"
    assert seen["write"] == "write"


def test_mcp_supervisor_sanitizes_remote_oauth_error():
    import asyncio
    from mcp.client.auth import OAuthTokenError
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import MCPServerConfig

    peer_secret = "PEER-BODY TOKEN-PATH QUERY-SECRET"

    async def exercise():
        client = MCPClient(
            MCPServerConfig(
                name="malicious-oauth",
                type="http",
                url="https://mcp.example/TOKEN-PATH?sig=QUERY-SECRET",
                timeout_seconds=1,
            )
        )

        async def fail_with_peer_error():
            raise OAuthTokenError(f"Token exchange failed (400): {peer_secret}")

        client._run_http = fail_with_peer_error
        await client.start()
        try:
            assert client.error == "mcp_reauthentication_required"
            assert client.error_kind == "needs_reauth"
            for secret in ("PEER-BODY", "TOKEN-PATH", "QUERY-SECRET"):
                assert secret not in repr(client.error)
        finally:
            await client.stop()

    asyncio.run(exercise())


def test_mcp_supervisor_sanitizes_remote_transient_stderr(capsys):
    import asyncio
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import MCPServerConfig

    peer_secret = "PEER-BODY TOKEN-PATH QUERY-SECRET"

    async def exercise():
        client = MCPClient(
            MCPServerConfig(
                name="malicious-peer",
                type="sse",
                url="https://mcp.example/TOKEN-PATH?sig=QUERY-SECRET",
                timeout_seconds=1,
            )
        )
        client._ready.set()
        client._session = object()

        async def fail_after_ready():
            client._shutdown.set()
            raise RuntimeError(peer_secret)

        client._run_sse = fail_after_ready
        await client.start()
        await client._supervisor_task
        assert client.error == "mcp_connection_transient"
        assert client.error_kind == "transient"

    asyncio.run(exercise())
    rendered = capsys.readouterr().err
    assert "transient connection failure" in rendered
    for secret in ("PEER-BODY", "TOKEN-PATH", "QUERY-SECRET"):
        assert secret not in rendered


@pytest.mark.parametrize(
    ("peer_error", "calls_per_execution", "expects_reconnect"),
    [
        ("PEER-BODY TOKEN-PATH QUERY-SECRET BEARER-TOKEN", 1, False),
        (
            "session expired PEER-BODY TOKEN-PATH QUERY-SECRET BEARER-TOKEN",
            2,
            True,
        ),
    ],
)
def test_registered_remote_mcp_tool_sanitizes_peer_failures_and_exception_graph(
    peer_error,
    calls_per_execution,
    expects_reconnect,
):
    import asyncio
    from mcp.types import Tool
    from openprogram.functions._runtime import (
        get,
        restore_registry,
        snapshot_registry,
    )
    from openprogram.mcp.adapter import register_remote_tool
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import MCPServerConfig

    snapshot = snapshot_registry()

    class PeerSession:
        calls = 0

        async def call_tool(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError(peer_error)

    async def exercise():
        client = MCPClient(
            MCPServerConfig(
                name="remote-peer",
                type="http",
                url="https://mcp.example/TOKEN-PATH?sig=QUERY-SECRET",
                timeout_seconds=1,
            )
        )
        session = PeerSession()
        client._session = session
        client._ready.set()
        registered_name = register_remote_tool(
            client,
            Tool(
                name="boom",
                description="remote failure",
                inputSchema={"type": "object", "properties": {}},
            ),
        )
        agent_tool = get(registered_name)
        result = await agent_tool.execute("call-1", {}, None, None)

        caught = None
        try:
            await client.call_tool("boom", {})
        except Exception as exc:  # noqa: BLE001 - inspect the public error graph
            caught = exc
        assert caught is not None

        rendered_result = repr(result)
        rendered_exception = "\n".join(
            (
                str(caught),
                repr(caught),
                "".join(traceback.format_exception(caught)),
            )
        )
        for secret in (
            "PEER-BODY",
            "TOKEN-PATH",
            "QUERY-SECRET",
            "BEARER-TOKEN",
        ):
            assert secret not in rendered_result
            assert secret not in rendered_exception
        assert "https://mcp.example" in rendered_result
        assert "https://mcp.example" in rendered_exception
        assert "RuntimeError" in rendered_result
        assert "RuntimeError" in rendered_exception
        assert caught.__cause__ is None
        assert caught.__context__ is None
        assert session.calls == calls_per_execution * 2
        assert client._reconnect_signal.is_set() is expects_reconnect

    try:
        asyncio.run(exercise())
    finally:
        restore_registry(snapshot)


@pytest.mark.parametrize(
    ("module_name", "class_name", "platform", "credentials", "sdk_prefix"),
    [
        (
            "openprogram.channels.implementations.slack",
            "SlackChannel",
            "slack",
            {"bot_token": "xoxb-test", "app_token": "xapp-test"},
            "slack_sdk",
        ),
        (
            "openprogram.channels.implementations.discord",
            "DiscordChannel",
            "discord",
            {"bot_token": "discord-test"},
            "discord",
        ),
    ],
)
def test_uninjectable_gateway_sdk_is_disabled_before_sdk_import(
    monkeypatch,
    module_name,
    class_name,
    platform,
    credentials,
    sdk_prefix,
):
    import builtins
    from openprogram.channels import accounts

    module = __import__(module_name, fromlist=[class_name])
    channel_class = getattr(module, class_name)
    monkeypatch.setattr(
        accounts,
        "load_credentials",
        lambda requested_platform, account_id: (
            credentials
            if (requested_platform, account_id) == (platform, "test")
            else {}
        ),
    )
    sdk_imports = []
    real_import = builtins.__import__

    def import_sentinel(name, *args, **kwargs):
        if name == sdk_prefix or name.startswith(f"{sdk_prefix}."):
            sdk_imports.append(name)
            raise AssertionError(f"disabled SDK imported: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_sentinel)

    with pytest.raises(URLPolicyError) as exc:
        channel_class("test")
    assert exc.value.reason == "UNMANAGED_TRANSPORT"
    assert sdk_imports == []


@pytest.mark.timeout(5)
def test_registered_bedrock_stream_is_disabled_before_aws_sdk_import(monkeypatch):
    import asyncio
    import builtins

    from openprogram.providers.api_registry import get_api_provider
    from openprogram.providers.types import Context, Model

    sdk_imports = []
    real_import = builtins.__import__

    def import_sentinel(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3.") or name.startswith("botocore."):
            sdk_imports.append(name)
            raise AssertionError(f"disabled AWS SDK imported: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_sentinel)
    provider = get_api_provider("bedrock-converse-stream")
    assert provider is not None
    model = Model(
        id="anthropic.claude-test-v1",
        name="Bedrock test",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )

    async def run():
        stream = provider.stream(model, Context(messages=[]), {})
        return await stream.result()

    with pytest.raises(URLPolicyError) as exc:
        asyncio.run(run())
    assert exc.value.reason == "UNMANAGED_TRANSPORT"
    assert sdk_imports == []


def test_bedrock_model_list_is_disabled_before_aws_sdk_import(monkeypatch):
    import builtins

    from openprogram.providers.amazon_bedrock import list_models

    sdk_imports = []
    real_import = builtins.__import__

    def import_sentinel(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3.") or name.startswith("botocore."):
            sdk_imports.append(name)
            raise AssertionError(f"disabled AWS SDK imported: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_sentinel)

    with pytest.raises(URLPolicyError) as exc:
        list_models.fetch("amazon-bedrock", 5.0)
    assert exc.value.reason == "UNMANAGED_TRANSPORT"
    assert sdk_imports == []
