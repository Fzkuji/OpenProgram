from __future__ import annotations

import importlib
import http.server
import json
import socketserver
import threading
import traceback
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


class _Response:
    status_code = 200
    reason_phrase = "OK"
    text = "{}"
    content = b"{}"

    def __init__(self, payload=None, content=None):
        self._payload = {} if payload is None else payload
        if content is not None:
            self.content = content
            self.text = content.decode("utf-8", errors="replace")

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None

    def read(self):
        return self.content

    def iter_bytes(self):
        yield self.content


class _Client:
    def __init__(self, calls, consumer, configured_origin=None, **_kwargs):
        calls.append(("client", consumer, configured_origin))
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def _response(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if "export.arxiv.org" in url:
            return _Response(
                content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )
        if url.endswith("/status"):
            return _Response({"status": "COMPLETED"})
        if url.endswith("/result"):
            return _Response({"images": []})
        if method == "POST" and "queue.fal.run" in url:
            return _Response(
                {
                    "status_url": "https://queue.fal.run/status",
                    "response_url": "https://queue.fal.run/result",
                }
            )
        return _Response()

    def get(self, url, **kwargs):
        return self._response("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._response("POST", url, kwargs)

    @contextmanager
    def stream(self, method, url, **kwargs):
        yield self._response(method, url, kwargs)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.requests = []
        self.redirect_configured = False
        self.redirect_location = None
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

    def handle_error(self, *_args):
        pass


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self):
        headers = {key.lower(): value for key, value in self.headers.items()}
        self.server.requests.append((self.command, self.path, headers))
        if self.server.redirect_configured:
            self.send_response(302)
            self.send_header(
                "Location",
                self.server.redirect_location
                or f"http://other.test:{self.server.port}/stolen",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/echo-error"):
            body = b"HEADER-SECRET QUERY-SECRET TOKEN-PATH"
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/non-2xx/"):
            status = int(self.path.split("/", 3)[2])
            body = b'{"web":{"results":[{"title":"PEER-BODY-SECRET"}]}}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.endswith("/status"):
            payload = {"status": "COMPLETED"}
        elif self.path.endswith("/result"):
            payload = {"images": []}
        elif self.command == "POST" and "/fal-ai/" in self.path:
            base = f"http://public.test:{self.server.port}"
            payload = {"status_url": base + "/status", "response_url": base + "/result"}
        elif "search_query=" in self.path:
            body = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif self.path == "/api.json":
            payload = {"openai": {"models": {"gpt-test": {"name": "GPT Test"}}}}
        else:
            payload = {}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply

    def log_message(self, *_args):
        pass


class _ReportedStream(httpcore.NetworkStream):
    def __init__(self, stream):
        self._stream = stream

    def read(self, *args, **kwargs):
        return self._stream.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        return self._stream.write(*args, **kwargs)

    def close(self):
        return self._stream.close()

    def start_tls(self, *args, **kwargs):
        return self._stream.start_tls(*args, **kwargs)

    def get_extra_info(self, info):
        if info == "server_addr":
            return ("93.184.216.34", 80)
        return self._stream.get_extra_info(info)


class _LoopbackBackend(httpcore.NetworkBackend):
    def __init__(self):
        self._real = httpcore.SyncBackend()

    def connect_tcp(self, _host, port, **kwargs):
        return _ReportedStream(self._real.connect_tcp("127.0.0.1", port, **kwargs))

    def connect_unix_socket(self, *args, **kwargs):
        return self._real.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds):
        self._real.sleep(seconds)


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
def real_managed_http(monkeypatch):
    original_client = safe_http.safe_client
    original_backend = safe_http.DecisionNetworkBackend
    monkeypatch.setattr(
        safe_http,
        "DecisionNetworkBackend",
        lambda decision: original_backend(decision, underlying=_LoopbackBackend()),
    )

    def factory(consumer, **kwargs):
        security = kwargs.pop("security", None) or OutboundSecurityConfig()
        kwargs["security"] = replace(
            security, resolver=lambda _hostname, _port: ("93.184.216.34",)
        )
        return original_client(consumer, **kwargs)

    monkeypatch.setattr(safe_http, "safe_client", factory)
    return factory


@pytest.fixture
def managed_clients(monkeypatch):
    calls = []

    def factory(consumer, **kwargs):
        return _Client(calls, consumer, **kwargs)

    monkeypatch.setattr(safe_http, "safe_client", factory)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("raw urllib used")),
    )
    return calls


def _fixed_test_origin(monkeypatch, consumer, port):
    origin = f"http://public.test:{port}"
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry[consumer]
    registry[consumer] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    return origin


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


@pytest.mark.parametrize("failure", ["missing", "FAILED", "CANCELLED"])
def test_fal_200_error_envelope_omits_peer_mapping(monkeypatch, failure):
    from openprogram.programs.tools.web.image_generate.providers import fal

    secret_mapping = {
        "error": "HEADER-SECRET",
        "request": "TOKEN-PATH?sig=QUERY-SECRET",
    }
    monkeypatch.setenv("FAL_KEY", "secret")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    if failure == "missing":
        monkeypatch.setattr(fal, "post_json", lambda *_args, **_kwargs: secret_mapping)
    else:
        monkeypatch.setattr(
            fal,
            "post_json",
            lambda *_args, **_kwargs: {
                "status_url": "https://queue.fal.run/status",
                "response_url": "https://queue.fal.run/result",
            },
        )
        monkeypatch.setattr(
            fal,
            "get_json",
            lambda *_args, **_kwargs: {"status": failure, **secret_mapping},
        )

    with pytest.raises(RuntimeError) as caught:
        fal.FalProvider().generate("draw")

    rendered = _render_exception(caught.value)
    expected = "missing queue URLs" if failure == "missing" else f"job {failure}"
    assert expected in rendered
    assert "HEADER-SECRET" not in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


def test_minimax_200_error_envelope_omits_peer_status_message(monkeypatch):
    from openprogram.programs.tools.web.web_search.providers import minimax

    monkeypatch.setenv("MINIMAX_CODE_PLAN_KEY", "secret")
    monkeypatch.setattr(
        minimax,
        "post_json",
        lambda *_args, **_kwargs: {
            "base_resp": {
                "status_code": 17,
                "status_msg": "HEADER-SECRET TOKEN-PATH?sig=QUERY-SECRET",
            }
        },
    )

    with pytest.raises(RuntimeError) as caught:
        minimax.MiniMaxProvider().search("query")

    rendered = _render_exception(caught.value)
    assert "MiniMax API error (17)" in rendered
    assert "HEADER-SECRET" not in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


def test_web_search_malformed_status_hides_signed_url_and_peer_echo(
    monkeypatch, malformed_server, real_managed_http
):
    from openprogram.programs.tools.web.web_search.providers import brave

    origin = _fixed_test_origin(
        monkeypatch, "tool.web_search.fixed_api", malformed_server.port
    )
    monkeypatch.setattr(brave, "API_URL", origin + "/TOKEN-PATH?sig=QUERY-SECRET")
    monkeypatch.setenv("BRAVE_API_KEY", "HEADER-SECRET")

    with pytest.raises(Exception) as caught:
        brave.BraveProvider().search("q")

    rendered = str(caught.value)
    assert "RemoteProtocolError" in rendered
    assert origin in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered


@pytest.mark.parametrize("status", [302, 304])
def test_brave_rejects_non_2xx_without_leaking_signed_url_or_body(
    monkeypatch, server, real_managed_http, status
):
    from openprogram.programs.tools.web.web_search._http import ProviderHTTPError
    from openprogram.programs.tools.web.web_search.providers import brave

    origin = _fixed_test_origin(monkeypatch, "tool.web_search.fixed_api", server.port)
    monkeypatch.setattr(
        brave,
        "API_URL",
        f"{origin}/non-2xx/{status}/TOKEN-PATH?sig=QUERY-SECRET",
    )
    monkeypatch.setenv("BRAVE_API_KEY", "HEADER-SECRET")

    with pytest.raises(ProviderHTTPError) as caught:
        brave.BraveProvider().search("query")

    rendered = _render_exception(caught.value)
    assert f"Brave HTTP {status}" in rendered
    assert origin in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered
    assert "PEER-BODY-SECRET" not in rendered


def test_image_api_4xx_body_hides_credentials_and_signed_url(
    monkeypatch, server, real_managed_http
):
    from openprogram.programs.tools.web.image_generate.providers import openai

    origin = _fixed_test_origin(monkeypatch, "tool.image_api.fixed", server.port)
    monkeypatch.setattr(
        openai, "API_URL", origin + "/echo-error/TOKEN-PATH?sig=QUERY-SECRET"
    )
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_provider_key",
        lambda _provider: "HEADER-SECRET",
    )

    with pytest.raises(Exception) as caught:
        openai.OpenAIImageProvider().generate("draw")

    rendered = str(caught.value)
    assert "HTTP 401" in rendered
    assert origin in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert "HEADER-SECRET" not in rendered


def test_codex_probe_hides_4xx_peer_body(monkeypatch, server):
    test_provider = importlib.import_module(
        "openprogram.webui._model_listing.test_provider"
    )
    base = f"http://127.0.0.1:{server.port}/echo-error"
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_api_key_with_auth_store",
        lambda _provider: "HEADER-SECRET",
    )
    monkeypatch.setattr(
        "openprogram.providers.storage._resolve_base_url", lambda _provider: base
    )

    result = test_provider._codex_ping("openai-codex", "gpt-test", 1)

    assert result["ok"] is False
    assert "HTTP 401" in result["error"]
    assert "HEADER-SECRET" not in result["error"]
    assert "QUERY-SECRET" not in result["error"]
    assert "TOKEN-PATH" not in result["error"]


def test_models_dev_public_loader_real_managed_success(
    monkeypatch, server, real_managed_http, _isolated_models_dev_cache
):
    models_dev = _isolated_models_dev_cache

    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(models_dev, "_CATALOGUE_URL", origin + "/api.json")
    monkeypatch.setattr(models_dev, "_write_disk_cache", lambda _data: None)
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["webui.model_listing.fixed"]
    registry["webui.model_listing.fixed"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)

    assert models_dev.lookup("openai", "gpt-test") == {"name": "GPT Test"}
    assert [(method, path) for method, path, _headers in server.requests] == [
        ("GET", "/api.json")
    ]
    assert server.requests[0][2]["host"] == f"public.test:{server.port}"


@pytest.mark.parametrize(
    ("module_name", "class_name", "env", "expected_consumer", "configured_origin"),
    [
        (
            "brave",
            "BraveProvider",
            {"BRAVE_API_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "exa",
            "ExaProvider",
            {"EXA_API_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "firecrawl",
            "FirecrawlProvider",
            {"FIRECRAWL_API_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "google",
            "GoogleProvider",
            {"GOOGLE_PSE_API_KEY": "secret", "GOOGLE_PSE_CX": "cx"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "minimax",
            "MiniMaxProvider",
            {"MINIMAX_CODE_PLAN_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "perplexity",
            "PerplexityProvider",
            {"PERPLEXITY_API_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "tavily",
            "TavilyProvider",
            {"TAVILY_API_KEY": "secret"},
            "tool.web_search.fixed_api",
            None,
        ),
        (
            "moonshot",
            "MoonshotProvider",
            {
                "KIMI_API_KEY": "secret",
                "MOONSHOT_BASE_URL": "http://127.0.0.1:19001/v1",
            },
            "tool.web_search.configured_api",
            "http://127.0.0.1:19001",
        ),
        (
            "ollama",
            "OllamaProvider",
            {"OLLAMA_API_KEY": "secret", "OLLAMA_BASE_URL": "http://127.0.0.1:19002"},
            "tool.web_search.configured_api",
            "http://127.0.0.1:19002",
        ),
        (
            "searxng",
            "SearxngProvider",
            {"SEARXNG_URL": "http://127.0.0.1:19003"},
            "tool.web_search.configured_api",
            "http://127.0.0.1:19003",
        ),
    ],
)
def test_web_search_adapters_use_registry_managed_http(
    monkeypatch,
    managed_clients,
    module_name,
    class_name,
    env,
    expected_consumer,
    configured_origin,
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module(
        f"openprogram.programs.tools.web.web_search.providers.{module_name}"
    )

    getattr(module, class_name)().search("query", num_results=1)

    assert managed_clients[0] == ("client", expected_consumer, configured_origin)
    assert any(call[0] in {"GET", "POST"} for call in managed_clients)


@pytest.mark.parametrize(
    ("module_name", "class_name", "env"),
    [
        ("arxiv", "ArxivProvider", {}),
        ("kagi", "KagiProvider", {"KAGI_API_KEY": "secret"}),
        ("jina", "JinaProvider", {"JINA_API_KEY": "secret"}),
        ("serper", "SerperProvider", {"SERPER_API_KEY": "secret"}),
        (
            "youcom",
            "YouComProvider",
            {"YDC_API_KEY": "secret", "YOU_API_KEY": "secret"},
        ),
    ],
)
def test_shared_http_search_adapters_use_registry_managed_http(
    monkeypatch, managed_clients, module_name, class_name, env
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module(
        f"openprogram.programs.tools.web.web_search.providers.{module_name}"
    )

    getattr(module, class_name)().search("query", num_results=1)

    assert managed_clients[0] == ("client", "tool.web_search.fixed_api", None)


@pytest.mark.parametrize(
    ("module_name", "class_name", "method"),
    [
        (
            "openprogram.programs.tools.web.image_generate.providers.fal",
            "FalProvider",
            "generate",
        ),
        (
            "openprogram.programs.tools.web.image_generate.providers.gemini",
            "GeminiImagenProvider",
            "generate",
        ),
        (
            "openprogram.programs.tools.web.image_generate.providers.openai",
            "OpenAIImageProvider",
            "generate",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.anthropic",
            "AnthropicVisionProvider",
            "analyze",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.gemini",
            "GeminiVisionProvider",
            "analyze",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.openai",
            "OpenAIVisionProvider",
            "analyze",
        ),
    ],
)
def test_image_api_adapters_use_fixed_registry_client(
    monkeypatch, managed_clients, module_name, class_name, method
):
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    monkeypatch.setattr(provider, "_resolve_key", lambda: "secret", raising=False)
    monkeypatch.setenv("FAL_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_provider_key",
        lambda _provider: "secret",
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    if method == "generate":
        getattr(provider, method)("draw", n=1)
    else:
        getattr(provider, method)([], "describe")

    assert managed_clients[0] == ("client", "tool.image_api.fixed", None)
    assert any(call[0] == "POST" for call in managed_clients)


@pytest.mark.parametrize(
    ("module_name", "class_name", "constant"),
    [
        ("brave", "BraveProvider", "API_URL"),
        ("exa", "ExaProvider", "API_URL"),
        ("firecrawl", "FirecrawlProvider", "API_URL"),
        ("google", "GoogleProvider", "API_URL"),
        ("minimax", "MiniMaxProvider", "API_URL_GLOBAL"),
        ("perplexity", "PerplexityProvider", "API_URL"),
        ("tavily", "TavilyProvider", "API_URL"),
    ],
)
def test_each_fixed_search_adapter_reaches_real_managed_transport(
    monkeypatch, server, real_managed_http, module_name, class_name, constant
):
    module = importlib.import_module(
        f"openprogram.programs.tools.web.web_search.providers.{module_name}"
    )
    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(module, constant, origin + "/api")
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.web_search.fixed_api"]
    registry["tool.web_search.fixed_api"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    for name in (
        "BRAVE_API_KEY",
        "EXA_API_KEY",
        "FIRECRAWL_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_PSE_API_KEY",
        "GOOGLE_PSE_CX",
        "MINIMAX_CODE_PLAN_KEY",
        "PERPLEXITY_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.setenv(name, "secret")

    getattr(module, class_name)().search("query", num_results=1)

    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    [
        ("arxiv", "ArxivProvider"),
        ("kagi", "KagiProvider"),
        ("jina", "JinaProvider"),
        ("serper", "SerperProvider"),
        ("youcom", "YouComProvider"),
    ],
)
def test_each_shared_http_search_adapter_reaches_real_managed_transport(
    monkeypatch, server, real_managed_http, module_name, class_name
):
    module = importlib.import_module(
        f"openprogram.programs.tools.web.web_search.providers.{module_name}"
    )
    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(module, "API_URL", origin + "/api")
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.web_search.fixed_api"]
    registry["tool.web_search.fixed_api"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    for name in (
        "KAGI_API_KEY",
        "JINA_API_KEY",
        "SERPER_API_KEY",
        "YDC_API_KEY",
        "YOU_API_KEY",
    ):
        monkeypatch.setenv(name, "secret")

    getattr(module, class_name)().search("query", num_results=1)

    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("module_name", "class_name", "constant"),
    [
        (
            "openprogram.programs.tools.web.image_generate.providers.gemini",
            "GeminiImagenProvider",
            "API_BASE",
        ),
        (
            "openprogram.programs.tools.web.image_generate.providers.openai",
            "OpenAIImageProvider",
            "API_URL",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.anthropic",
            "AnthropicVisionProvider",
            "API_URL",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.gemini",
            "GeminiVisionProvider",
            "API_BASE",
        ),
        (
            "openprogram.programs.tools.web.image_analyze.providers.openai",
            "OpenAIVisionProvider",
            "API_URL",
        ),
    ],
)
def test_each_image_adapter_reaches_real_managed_transport(
    monkeypatch, server, real_managed_http, module_name, class_name, constant
):
    module = importlib.import_module(module_name)
    provider = getattr(module, class_name)()
    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(module, constant, origin + "/api")
    monkeypatch.setattr(provider, "_resolve_key", lambda: "secret", raising=False)
    monkeypatch.setattr(
        "openprogram.providers.env_api_keys.resolve_provider_key",
        lambda _provider: "secret",
    )
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.image_api.fixed"]
    registry["tool.image_api.fixed"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)

    if hasattr(provider, "generate"):
        provider.generate("draw", n=1)
    else:
        provider.analyze([], "describe")

    assert len(server.requests) == 1


def test_fal_multihop_adapter_reaches_real_managed_transport(
    monkeypatch, server, real_managed_http
):
    from openprogram.programs.tools.web.image_generate.providers import fal

    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(fal, "QUEUE_BASE", origin)
    monkeypatch.setenv("FAL_KEY", "secret")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.image_api.fixed"]
    registry["tool.image_api.fixed"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)

    fal.FalProvider().generate("draw", n=1)

    assert [request[0] for request in server.requests] == ["POST", "GET", "GET"]


def test_configured_search_keeps_credential_on_exact_origin_and_rejects_redirect(
    monkeypatch, server
):
    from openprogram.programs.tools.web.web_search.providers import ollama

    monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("OLLAMA_API_KEY", "TOKEN")
    server.redirect_configured = True

    with pytest.raises(Exception, match="REDIRECT_ORIGIN_FORBIDDEN"):
        ollama.OllamaProvider().search("query", num_results=1)

    assert len(server.requests) == 1
    assert server.requests[0][2]["authorization"] == "Bearer TOKEN"


def test_configured_search_passes_explicit_owner_exception(monkeypatch):
    from openprogram.programs.tools.web.web_search.providers import ollama
    from openprogram.security.url_policy import OwnerURLException

    calls = []

    def factory(consumer, configured_url, *, owner_exception):
        calls.append((consumer, configured_url, owner_exception))
        return _Client([], consumer)

    monkeypatch.setattr(safe_http, "configured_safe_client", factory)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:19020/api")
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")

    ollama.OllamaProvider().search("query", num_results=1)

    consumer, configured_url, exception = calls[0]
    assert consumer == "tool.web_search.configured_api"
    assert configured_url == "http://127.0.0.1:19020/api"
    assert type(exception) is OwnerURLException
    assert exception.consumer == consumer
    assert exception.origin == "http://127.0.0.1:19020"


def test_fixed_search_rejects_private_redirect_before_second_request(
    monkeypatch, server, real_managed_http
):
    from openprogram.programs.tools.web.web_search.providers import brave

    origin = f"http://public.test:{server.port}"
    monkeypatch.setattr(brave, "API_URL", origin + "/api")
    monkeypatch.setenv("BRAVE_API_KEY", "TOKEN")
    registry = dict(safe_http.CONSUMER_REGISTRY)
    spec = registry["tool.web_search.fixed_api"]
    registry["tool.web_search.fixed_api"] = replace(
        spec,
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        fixed_origins=frozenset({origin}),
    )
    monkeypatch.setattr(safe_http, "CONSUMER_REGISTRY", registry)
    server.redirect_configured = True
    server.redirect_location = f"http://127.0.0.1:{server.port}/private"

    with pytest.raises(Exception, match="REDIRECT_ORIGIN_FORBIDDEN"):
        brave.BraveProvider().search("query", num_results=1)

    assert len(server.requests) == 1
    assert server.requests[0][2]["x-subscription-token"] == "TOKEN"


def test_fixed_search_rejects_nonshipped_origin_before_dns(
    monkeypatch, real_managed_http
):
    from openprogram.programs.tools.web.web_search.providers import brave

    monkeypatch.setattr(brave, "API_URL", "https://other.test/api")
    monkeypatch.setenv("BRAVE_API_KEY", "TOKEN")

    with pytest.raises(Exception, match="FIXED_ORIGIN_MISMATCH"):
        brave.BraveProvider().search("query", num_results=1)
