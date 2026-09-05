from __future__ import annotations

import asyncio
import http.server
import ipaddress
import io
import json
import socketserver
import threading
import traceback
import zipfile
from contextlib import contextmanager
from dataclasses import replace

import httpcore
import pytest

from openprogram.security import safe_http
from openprogram.security.safe_http import OutboundSecurityConfig


def _reset_models_dev_cache(models_dev) -> None:
    with models_dev._cache_lock:
        models_dev._cache.update({
            "data": None,
            "fetched_at": 0.0,
            "last_attempt_at": 0.0,
            "refreshing": False,
        })


@pytest.fixture
def _isolated_models_dev_cache(monkeypatch, tmp_path):
    from openprogram.providers.sources import models_dev

    monkeypatch.setattr(
        models_dev, "_disk_cache_path", lambda: tmp_path / "models_dev.json"
    )
    _reset_models_dev_cache(models_dev)
    yield models_dev
    _reset_models_dev_cache(models_dev)


def _repo_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "repo-main/demo/SKILL.md",
            "---\nname: demo\ndescription: demo\n---\nbody\n",
        )
    return output.getvalue()


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.mode = "zip"
        self.requests = []
        super().__init__(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server_address[1]

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path.startswith("/redirect-without-location/"):
            status = int(self.path.split("/", 3)[2])
            body = b'{"servers":[{"name":"PEER-BODY-SECRET"}]}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/echo-error"):
            body, content_type = (
                b"HEADER-SECRET QUERY-SECRET TOKEN-PATH",
                "application/json",
            )
            self.send_response(401)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.server.mode in {"index", "index_wrong_mime", "index_invalid"}:
            body = (
                b'{"skills":[{"name":{"echo":"QUERY-SECRET"},"files":[]}]}'
                if self.server.mode == "index_invalid"
                else b'{"skills": []}'
            )
            content_type = (
                "text/html"
                if self.server.mode == "index_wrong_mime"
                else "application/json"
            )
        else:
            body = _repo_zip()
            content_type = (
                "text/html" if self.server.mode == "wrong_mime" else "application/zip"
            )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _AsyncReportedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream):
        self._stream = stream

    async def read(self, *args, **kwargs):
        return await self._stream.read(*args, **kwargs)

    async def write(self, *args, **kwargs):
        return await self._stream.write(*args, **kwargs)

    async def aclose(self):
        return await self._stream.aclose()

    async def start_tls(self, *args, **kwargs):
        return await self._stream.start_tls(*args, **kwargs)

    def get_extra_info(self, info):
        if info == "server_addr":
            return ("93.184.216.34", 80)
        return self._stream.get_extra_info(info)


class _AsyncLoopbackBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self._real = httpcore.AnyIOBackend()

    async def connect_tcp(self, _host, port, **kwargs):
        stream = await self._real.connect_tcp("127.0.0.1", port, **kwargs)
        return _AsyncReportedStream(stream)

    async def connect_unix_socket(self, *args, **kwargs):
        return await self._real.connect_unix_socket(*args, **kwargs)

    async def sleep(self, seconds):
        await self._real.sleep(seconds)


class _MalformedHandler(socketserver.BaseRequestHandler):
    def handle(self):
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
        self.requests = []
        super().__init__(("127.0.0.1", 0), _MalformedHandler)
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
def real_async_managed(monkeypatch):
    original_client = safe_http.safe_async_client
    original_backend = safe_http.AsyncDecisionNetworkBackend
    monkeypatch.setattr(
        safe_http,
        "AsyncDecisionNetworkBackend",
        lambda decision: original_backend(decision, underlying=_AsyncLoopbackBackend()),
    )

    def factory(consumer, **kwargs):
        security = kwargs.pop("security", None) or OutboundSecurityConfig()
        kwargs["security"] = replace(
            security, resolver=lambda _hostname, _port: ("93.184.216.34",)
        )
        return original_client(consumer, **kwargs)

    monkeypatch.setattr(safe_http, "safe_async_client", factory)
    return factory


class _Response:
    status_code = 200
    reason_phrase = "OK"

    def __init__(self, url):
        self.url = url
        self.content = _repo_zip() if "codeload.github.com" in url else b"{}"
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = {
            "content-type": (
                "application/zip"
                if "codeload.github.com" in url
                else "application/json"
            )
        }

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.text)

    def iter_bytes(self):
        yield self.content


class _AsyncClient:
    def __init__(self, calls, consumer, configured_origin=None, **_kwargs):
        calls.append(("async_client", consumer, configured_origin))
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response(url)


class _Client:
    def __init__(self, calls, consumer, configured_origin=None, **_kwargs):
        calls.append(("client", consumer, configured_origin))
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response(url)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _Response(url)

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        yield _Response(url)


@pytest.fixture
def managed_clients(monkeypatch):
    calls = []
    monkeypatch.setattr(
        safe_http,
        "safe_async_client",
        lambda consumer, **kwargs: _AsyncClient(calls, consumer, **kwargs),
    )
    monkeypatch.setattr(
        safe_http,
        "safe_client",
        lambda consumer, **kwargs: _Client(calls, consumer, **kwargs),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("raw urllib used")),
    )
    return calls


def test_skills_github_archive_uses_fixed_catalog_client(managed_clients):
    from openprogram.skills import discovery

    entries = discovery.browse("github://owner/repo")

    assert entries[0]["name"] == "demo"
    assert managed_clients[0] == ("async_client", "skills.github.catalog", None)


def test_skills_configured_index_freezes_owner_origin(managed_clients):
    from openprogram.skills import discovery

    discovery.browse("http://127.0.0.1:19010/index.json")

    assert managed_clients[0] == (
        "async_client",
        "skills.configured.catalog",
        "http://127.0.0.1:19010",
    )


def test_plugin_marketplace_uses_configured_catalog_client(
    monkeypatch, managed_clients
):
    from openprogram.plugins import marketplace

    monkeypatch.setattr(
        marketplace,
        "get_marketplace",
        lambda _mid: {"url": "http://127.0.0.1:19011/catalog.json"},
    )
    asyncio.run(marketplace.fetch_index("local"))

    assert managed_clients[0] == (
        "async_client",
        "plugins.marketplace",
        "http://127.0.0.1:19011",
    )


@pytest.mark.parametrize(
    ("call", "consumer"),
    [
        ("plugin_pip", "plugins.autoupdate"),
        ("plugin_npm", "plugins.autoupdate"),
        ("github_release", "updater.github"),
    ],
)
def test_update_metadata_uses_fixed_registry_client(managed_clients, call, consumer):
    if call.startswith("plugin"):
        from openprogram.plugins import autoupdate

        if call == "plugin_pip":
            autoupdate._check_pip("demo", "0")
        else:
            autoupdate._check_npm("demo", "0")
    else:
        from openprogram.updater import github

        github.latest_release()
    assert managed_clients[0] == ("client", consumer, None)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("plugin_pip", "9.9.9"),
        ("plugin_npm", "9.9.9"),
        ("github_release", "release-payload"),
    ],
)
@pytest.mark.parametrize(
    ("mime", "accepted"),
    [
        ("application/json", True),
        ("application/problem+json; charset=utf-8", True),
        ("text/problem+json", False),
        ("text/html", False),
    ],
)
def test_update_metadata_requires_json_mime(
    monkeypatch, call, expected, mime, accepted
):
    payload = {
        "info": {"version": "9.9.9"},
        "version": "9.9.9",
        "tag_name": "v9.9.9",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "demo.zip",
                "browser_download_url": "https://downloads.example/demo.zip",
            }
        ],
    }

    class Response:
        status_code = 200
        reason_phrase = "OK"
        headers = {"content-type": mime}
        url = "https://api.example/metadata"

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(safe_http, "safe_client", lambda *_args, **_kwargs: Client())

    if call.startswith("plugin"):
        from openprogram.plugins import autoupdate

        result = (
            autoupdate._check_pip("demo", "0")
            if call == "plugin_pip"
            else autoupdate._check_npm("demo", "0")
        )
    else:
        from openprogram.updater import github

        result = github.latest_release()
    wanted = payload if expected == "release-payload" else expected
    assert result == (wanted if accepted else None)


def test_model_listing_openai_compat_freezes_configured_origin(
    monkeypatch, managed_clients
):
    from openprogram.webui._model_listing.fetchers import openai_compat

    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_api_key_with_auth_store",
        lambda _provider: "secret",
    )
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url",
        lambda _provider: "http://127.0.0.1:19012/v1",
    )
    openai_compat._fetch_openai_compat("custom", 1)

    assert (
        "client",
        "webui.model_listing.configured",
        "http://127.0.0.1:19012",
    ) in managed_clients


def test_model_listing_credential_probe_uses_configured_registry_client(
    monkeypatch, managed_clients
):
    from openprogram.webui._model_listing import credentials

    monkeypatch.setattr(credentials, "_kind_for", lambda _provider: "openai_bearer")
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url",
        lambda _provider: "http://127.0.0.1:19013/v1",
    )
    credentials.validate_credential(
        "custom", api_key="secret", use_cache=False, timeout=1
    )

    assert managed_clients[0] == (
        "client",
        "webui.model_listing.configured",
        "http://127.0.0.1:19013",
    )


def test_model_listing_fixed_google_probe_uses_fixed_registry_client(
    monkeypatch, managed_clients
):
    from openprogram.webui._model_listing import credentials

    monkeypatch.setattr(credentials, "_kind_for", lambda _provider: "google_query")
    credentials.validate_credential(
        "google", api_key="secret", use_cache=False, timeout=1
    )

    assert managed_clients[0] == (
        "client",
        "webui.model_listing.fixed",
        None,
    )


def test_models_dev_public_loader_uses_fixed_registry_client(
    monkeypatch, managed_clients, _isolated_models_dev_cache
):
    models_dev = _isolated_models_dev_cache

    models_dev.lookup("openai", "gpt-test")

    assert managed_clients[0] == (
        "client",
        "webui.model_listing.fixed",
        None,
    )


def test_codex_connectivity_probe_uses_configured_registry_client(
    monkeypatch, managed_clients
):
    import importlib

    test_provider = importlib.import_module(
        "openprogram.webui._model_listing.test_provider"
    )

    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_api_key_with_auth_store",
        lambda _provider: "secret",
    )
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url",
        lambda _provider: "http://127.0.0.1:19014/backend-api",
    )
    test_provider._codex_ping("openai-codex", "gpt-test", 1)

    assert managed_clients[0] == (
        "client",
        "webui.model_listing.configured",
        "http://127.0.0.1:19014",
    )


def test_configured_credential_probe_hides_4xx_peer_body(monkeypatch, server):
    from openprogram.webui._model_listing import credentials

    base = f"http://127.0.0.1:{server.port}/echo-error"
    monkeypatch.setattr(credentials, "_kind_for", lambda _provider: "openai_bearer")
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url", lambda _provider: base
    )

    result = credentials.validate_credential(
        "custom", api_key="HEADER-SECRET", use_cache=False, timeout=1
    )

    assert result.http_status == 401
    assert "HEADER-SECRET" not in (result.detail or "")
    assert "QUERY-SECRET" not in (result.detail or "")
    assert "TOKEN-PATH" not in (result.detail or "")


def test_configured_model_fetcher_hides_4xx_peer_body(monkeypatch, server):
    from openprogram.webui._model_listing.fetchers import openai_compat

    base = f"http://127.0.0.1:{server.port}/echo-error"
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_api_key_with_auth_store",
        lambda _provider: "HEADER-SECRET",
    )
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url", lambda _provider: base
    )

    result = openai_compat._fetch_openai_compat("custom", 1)

    assert "HTTP 401" in result["error"]
    assert "HEADER-SECRET" not in result["error"]
    assert "QUERY-SECRET" not in result["error"]
    assert "TOKEN-PATH" not in result["error"]


@pytest.mark.parametrize("status", [302, 304])
def test_configured_model_fetcher_rejects_non_2xx_without_leaking(
    monkeypatch, server, status
):
    from openprogram.webui._model_listing.fetchers import openai_compat

    origin = f"http://127.0.0.1:{server.port}"
    base = f"{origin}/redirect-without-location/{status}/TOKEN-PATH?sig=QUERY-SECRET"
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_api_key_with_auth_store",
        lambda _provider: "HEADER-SECRET",
    )
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url", lambda _provider: base
    )

    result = openai_compat._fetch_openai_compat("custom", 1)

    assert f"HTTP {status}" in result["error"]
    assert origin in result["error"]
    assert "accepted-302" not in repr(result)
    assert "TOKEN-PATH" not in result["error"]
    assert "QUERY-SECRET" not in result["error"]
    assert "HEADER-SECRET" not in result["error"]
    assert "PEER-BODY-SECRET" not in result["error"]


def test_web_mcp_catalog_route_uses_configured_registry_client(
    monkeypatch, managed_clients, tmp_path
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes import mcp
    from openprogram import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get(
        "/api/mcp/catalog?url=http://127.0.0.1:19015/catalog.json"
    )

    assert response.status_code == 200
    assert managed_clients[0] == (
        "async_client",
        "webui.mcp.catalog",
        "http://127.0.0.1:19015",
    )


def test_web_mcp_catalog_malformed_status_hides_signed_url_and_peer_echo(
    malformed_server, tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram import paths
    from openprogram.webui.routes import mcp

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    app = FastAPI()
    mcp.register(app)
    url = f"http://127.0.0.1:{malformed_server.port}/TOKEN-PATH?sig=QUERY-SECRET"

    response = TestClient(app).get("/api/mcp/catalog", params={"url": url})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "RemoteProtocolError" in detail
    assert f"http://127.0.0.1:{malformed_server.port}" in detail
    assert "TOKEN-PATH" not in detail
    assert "QUERY-SECRET" not in detail


@pytest.mark.parametrize("status", [302, 304])
def test_web_mcp_catalog_rejects_non_2xx_without_leaking_signed_url_or_body(
    server, tmp_path, monkeypatch, status
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram import paths
    from openprogram.webui.routes import mcp

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    app = FastAPI()
    mcp.register(app)
    origin = f"http://127.0.0.1:{server.port}"
    url = f"{origin}/redirect-without-location/{status}/TOKEN-PATH?sig=QUERY-SECRET"

    response = TestClient(app).get("/api/mcp/catalog", params={"url": url})

    assert response.status_code == 502
    rendered_response = response.text
    assert f"HTTP {status}" in rendered_response
    assert origin in rendered_response
    assert "TOKEN-PATH" not in rendered_response
    assert "QUERY-SECRET" not in rendered_response
    assert "PEER-BODY-SECRET" not in rendered_response

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(mcp._fetch_catalog_json(url))

    rendered_error = _render_exception(caught.value)
    assert f"HTTP {status}" in rendered_error
    assert origin in rendered_error
    assert "TOKEN-PATH" not in rendered_error
    assert "QUERY-SECRET" not in rendered_error
    assert "PEER-BODY-SECRET" not in rendered_error


def _render_exception(error: BaseException) -> str:
    return "\n".join(
        (
            str(error),
            repr(error),
            "".join(traceback.format_exception(error)),
            repr(error.__cause__),
            repr(error.__context__),
        )
    )


def test_skills_cli_status_error_hides_signed_source_url(server):
    from openprogram.cli.commands.skills import _cmd_skills_search

    url = f"http://127.0.0.1:{server.port}/echo-error/TOKEN-PATH?sig=QUERY-SECRET"

    with pytest.raises(Exception) as caught:
        _cmd_skills_search("", source=url)

    rendered = _render_exception(caught.value)
    assert "HTTP 401 Unauthorized" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered


def test_skills_rejects_redirect_without_location_as_sanitized_status(server):
    from openprogram.skills import discovery

    url = (
        f"http://127.0.0.1:{server.port}/redirect-without-location/302/"
        "TOKEN-PATH?sig=QUERY-SECRET"
    )

    with pytest.raises(Exception) as caught:
        discovery.browse(url)

    rendered = _render_exception(caught.value)
    assert "HTTP 302 Found" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


def test_skills_web_status_error_hides_signed_source_url(server):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes import skills

    app = FastAPI()
    skills.register(app)
    url = f"http://127.0.0.1:{server.port}/echo-error/TOKEN-PATH?sig=QUERY-SECRET"

    response = TestClient(app).get("/api/skills/discovery/browse", params={"url": url})

    assert response.status_code == 502
    rendered = response.text
    assert "HTTP 401 Unauthorized" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered


@pytest.mark.parametrize("operation", ["browse", "pull", "install"])
def test_skills_invalid_index_hides_peer_values_from_runtime_entries(server, operation):
    from openprogram.skills import discovery

    server.mode = "index_invalid"
    url = f"http://127.0.0.1:{server.port}/catalog.json?sig=QUERY-SECRET"

    with pytest.raises(RuntimeError) as caught:
        if operation == "browse":
            discovery.browse(url)
        elif operation == "pull":
            discovery.pull(url)
        else:
            discovery.install_one(url, "demo")

    rendered = _render_exception(caught.value)
    assert "Invalid skill index" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "QUERY-SECRET" not in rendered
    assert "input_value" not in rendered


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/skills/discovery/browse", None),
        ("post", "/api/skills/discovery/pull", {}),
        ("post", "/api/skills/discovery/install", {"name": "demo"}),
    ],
)
def test_skills_invalid_index_hides_peer_values_from_web_routes(
    server, method, path, body
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes import skills

    server.mode = "index_invalid"
    url = f"http://127.0.0.1:{server.port}/catalog.json?sig=QUERY-SECRET"
    app = FastAPI()
    skills.register(app)
    client = TestClient(app)
    response = (
        client.get(path, params={"url": url})
        if method == "get"
        else client.post(path, json={"url": url, **(body or {})})
    )

    assert response.status_code == 502
    rendered = response.text
    assert "Invalid skill index" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "QUERY-SECRET" not in rendered
    assert "input_value" not in rendered


def test_skills_invalid_index_hides_peer_values_from_cli(server):
    from openprogram.cli.commands.skills import _cmd_skills_install, _cmd_skills_search

    server.mode = "index_invalid"
    url = f"http://127.0.0.1:{server.port}/catalog.json?sig=QUERY-SECRET"

    for call in (
        lambda: _cmd_skills_search("", source=url),
        lambda: _cmd_skills_install("demo", source=url),
    ):
        with pytest.raises(RuntimeError) as caught:
            call()
        rendered = _render_exception(caught.value)
        assert "Invalid skill index" in rendered
        assert f"http://127.0.0.1:{server.port}" in rendered
        assert "QUERY-SECRET" not in rendered
        assert "input_value" not in rendered


def test_marketplace_cli_and_web_status_errors_hide_signed_source_url(
    monkeypatch, server, capsys
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.cli.commands.plugins import _cmd_plugins_search
    from openprogram.plugins import marketplace
    from openprogram.webui.routes import plugins

    url = f"http://127.0.0.1:{server.port}/echo-error/TOKEN-PATH?sig=QUERY-SECRET"
    entry = {"id": "signed", "name": "signed", "url": url}
    monkeypatch.setattr(marketplace, "_load", lambda: [entry])

    assert _cmd_plugins_search("demo") == 0
    cli_rendered = capsys.readouterr().err

    app = FastAPI()
    plugins.register(app)
    response = TestClient(app).get("/api/plugins/marketplace/signed/index")

    assert response.status_code == 502
    rendered = cli_rendered + response.text
    assert "HTTP 401 Unauthorized" in rendered
    assert f"http://127.0.0.1:{server.port}" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered


def _github_over_real_managed(monkeypatch, server, *, body_cap=1024 * 1024):
    from openprogram.skills import discovery

    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(
        discovery.GhRepo,
        "zip_url",
        property(lambda _self: origin + "/archive.zip"),
    )
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["skills.github.catalog"]
    registry["skills.github.catalog"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
        max_decoded_body_bytes=body_cap,
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    discovery._ZIP_CACHE.clear()
    return discovery


def test_skills_github_archive_real_managed_success(
    monkeypatch, server, real_async_managed
):
    discovery = _github_over_real_managed(monkeypatch, server)

    entries = discovery.browse("github://owner/repo")

    assert entries[0]["name"] == "demo"
    assert server.requests == ["/archive.zip"]


def test_skills_github_archive_rejects_wrong_mime(
    monkeypatch, server, real_async_managed
):
    discovery = _github_over_real_managed(monkeypatch, server)
    server.mode = "wrong_mime"

    with pytest.raises(Exception, match="MIME|archive"):
        discovery.browse("github://owner/repo")


def test_skills_github_archive_enforces_decoded_cap(
    monkeypatch, server, real_async_managed
):
    discovery = _github_over_real_managed(monkeypatch, server, body_cap=16)

    with pytest.raises(Exception, match="BODY_TOO_LARGE"):
        discovery.browse("github://owner/repo")


def test_skills_configured_local_index_real_managed_success(monkeypatch, server):
    from openprogram.skills import discovery

    server.mode = "index"
    clients = []
    original = safe_http.safe_async_client

    def factory(consumer, **kwargs):
        client = original(consumer, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(safe_http, "safe_async_client", factory)

    assert discovery.browse(f"http://127.0.0.1:{server.port}/index.json") == []
    assert len(clients) == 1
    assert clients[0].audit_events[0].reason == "ALLOWED"


def test_skills_configured_index_rejects_wrong_json_mime(monkeypatch, server):
    from openprogram.skills import discovery

    server.mode = "index_wrong_mime"

    with pytest.raises(Exception, match="MIME"):
        discovery.browse(f"http://127.0.0.1:{server.port}/index.json")


def test_web_mcp_catalog_rejects_wrong_json_mime(monkeypatch, server, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram import paths
    from openprogram.webui.routes import mcp

    server.mode = "index_wrong_mime"
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get(
        "/api/mcp/catalog",
        params={"url": f"http://127.0.0.1:{server.port}/catalog.json"},
    )

    assert response.status_code == 502
    assert "URLPolicyError" in response.json()["detail"]


def test_web_mcp_diff_keeps_same_origin_catalog_errors_distinct(
    monkeypatch, server, tmp_path
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram import paths
    from openprogram.mcp.config import MCPServerConfig, save_configs
    from openprogram.webui.routes import mcp

    origin = f"http://127.0.0.1:{server.port}"
    sources = [
        origin + "/echo-error/a/TOKEN-PATH?sig=QUERY-SECRET-A",
        origin + "/echo-error/b/TOKEN-PATH?sig=QUERY-SECRET-B",
    ]
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    save_configs(
        [
            MCPServerConfig(
                name=f"remote-{index}",
                type="http",
                url="https://mcp.example/mcp",
                source_catalog_url=source,
            )
            for index, source in enumerate(sources, 1)
        ]
    )
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get("/api/mcp/catalog/diff")

    assert response.status_code == 200
    errors = response.json()["catalog_errors"]
    assert list(errors) == [origin, f"{origin}#2"]
    assert len(errors) == 2
    rendered = json.dumps(errors)
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET-A" not in rendered
    assert "QUERY-SECRET-B" not in rendered
