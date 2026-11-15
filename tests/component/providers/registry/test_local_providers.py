import pytest

from openprogram.models.providers.catalog.metadata import provider_base_url, shipped_provider_ids


@pytest.mark.parametrize('provider,port', [('ollama',11434),('lmstudio',1234),('vllm',8000),('llamacpp',8080),('local',8000)])
def test_local_presets_are_available_offline(provider, port):
    assert provider in shipped_provider_ids()
    assert provider_base_url(provider) == f'http://localhost:{port}/v1'


@pytest.fixture
def local_server(monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from openprogram.models.providers.settings import storage

    class RequestLog(list):
        pass
    requests = RequestLog()
    payload = {"data": [{"id": "local-test", "context_length": 8192}]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, dict(self.headers), None))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            if body.get('stream'):
                chunk = {'id':'test','object':'chat.completion.chunk','created':1,'model':'local-test','choices':[{'index':0,'delta':{'role':'assistant','content':'local response'},'finish_reason':None}]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                if body.get('tools'):
                    chunk['choices'] = [{'index':0,'delta':{'tool_calls':[{'index':0,'id':'call-local','type':'function','function':{'name':'echo','arguments':'{"value":"ok"}'}}]},'finish_reason':None}]
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                chunk['choices'] = [{'index':0,'delta':{},'finish_reason':'tool_calls' if body.get('tools') else 'stop'}]
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
            else:
                post_payload = getattr(requests, 'post_payload', {'choices':[{'message':{'role':'assistant','content':'pong'},'finish_reason':'stop'}]})
                self.wfile.write(post_payload.encode() if isinstance(post_payload, str) else json.dumps(post_payload).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(storage, '_read_providers_cfg', lambda: {'ollama': {'base_url':base}})
    monkeypatch.setattr('openprogram.models.providers.settings.env_api_keys.resolve_api_key_with_auth_store', lambda *a: None)
    monkeypatch.setattr('openprogram.models.providers.settings.env_api_keys.resolve_provider_key', lambda *a: None)
    monkeypatch.setattr('openprogram.models.auth.usage.acquire_pooled', lambda *a: None)
    requests.payload = payload
    yield base, requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_discovery_and_connectivity_without_key(local_server):
    from openprogram.models.listing.fetchers.openai_compat import _fetch_openai_compat
    from openprogram.models.listing.credentials import validate_credential
    base, requests = local_server
    result = _fetch_openai_compat('ollama', 3)
    assert result[0]['id'] == 'local-test'
    assert validate_credential('ollama', model='local-test', use_cache=False, timeout=3).ok
    assert requests[0][0] == '/v1/models'
    assert requests[1][0] == '/v1/chat/completions'
    assert all('Authorization' not in headers for _, headers, _ in requests)


@pytest.mark.parametrize('key', [None, 'server-key'])
def test_public_stream_uses_local_endpoint_and_optional_key(local_server, monkeypatch, key):
    from openprogram.models.providers.streaming.stream import stream_simple
    from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, UserMessage
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-cloud-secret')
    base, requests = local_server
    model = Model(id='local-test', name='Local', provider='ollama', api='openai-completions', base_url='http://localhost:11434/v1', context_window=4096, max_tokens=1024)
    import asyncio
    async def collect():
        return [event async for event in stream_simple(model, Context(messages=[UserMessage(content='Hello', timestamp=1)]), SimpleStreamOptions(api_key=key, max_tokens=10))]
    events = asyncio.run(collect())
    assert events[-1].type == 'done', events[-1]
    assert requests[-1][0] == '/v1/chat/completions'
    assert requests[-1][1].get('Authorization') == (f'Bearer {key}' if key else None)
    assert requests[-1][2]['model'] == 'local-test'


def test_local_manual_limits_and_address_reload(monkeypatch):
    from openprogram.state import config as user_config
    from openprogram.models.providers.settings import storage
    from openprogram.models.providers.catalog import enabled_models
    config = {'providers': {'ollama': {'enabled': True, 'models': []}}}
    monkeypatch.setattr(user_config, 'read_config', lambda: config)
    monkeypatch.setattr(user_config, 'update_config', lambda fn: fn(config))
    monkeypatch.setattr('openprogram.models.providers.settings.config_read.read_providers_config', lambda: config['providers'])
    monkeypatch.setattr(storage, '_read_providers_cfg', lambda: config['providers'])
    assert storage.add_manual_model('ollama', 'qwen:latest', context_window=8192, max_tokens=2048)['ok']
    storage.set_provider_config('ollama', {'base_url':'http://127.0.0.1:12345'})
    model = enabled_models.ENABLED_MODELS['ollama/qwen:latest']
    assert model.base_url == 'http://127.0.0.1:12345/v1'
    assert model.context_window == 8192
    assert model.max_tokens == 2048
    assert not storage.add_manual_model('ollama', 'bad', context_window=True)['ok']


def test_invalid_local_urls_are_not_persisted(monkeypatch):
    from openprogram.models.providers.settings.storage import create_custom_provider, set_provider_config
    for url in ("file:///tmp/model", "http://user:password@localhost:1234", "http://localhost:bad", "http://localhost:1234?key=secret"):
        assert "error" in create_custom_provider("", "test", url, local=True)
        assert "error" in set_provider_config("ollama", {"base_url": url})


def test_public_stream_preserves_local_tool_calls(local_server):
    import asyncio
    from openprogram.models.providers.streaming.stream import stream_simple
    from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, UserMessage, Tool
    model = Model(id="local-test", name="Local", provider="ollama", api="openai-completions", base_url=local_server[0]+"/v1")
    async def collect():
        return [event async for event in stream_simple(model, Context(messages=[UserMessage(content="Use echo", timestamp=1)], tools=[Tool(name="echo", description="Echo", parameters={"type":"object", "properties":{"value":{"type":"string"}}})]), SimpleStreamOptions(max_tokens=32))]
    events = asyncio.run(collect())
    assert events[-1].type == "done", events[-1]
    assert events[-1].message.stop_reason == "toolUse"
    calls = [item for item in events[-1].message.content if item.type == "toolCall"]
    assert calls[0].name == "echo"
    assert calls[0].arguments == {"value":"ok"}


@pytest.mark.parametrize("payload", [{"error":"server failed"}, {}, {"data":{}}, {"data":None}, {"data":[]}])
def test_discovery_rejects_malformed_envelopes_but_accepts_empty_list(local_server, payload):
    from openprogram.models.listing.fetchers import fetch_and_normalize
    _, requests = local_server
    requests.payload.clear()
    requests.payload.update(payload)
    result = fetch_and_normalize("ollama", timeout=3)
    if payload == {"data":[]}:
        assert result == {"models":[]}
    else:
        assert "error" in result, result


@pytest.mark.parametrize("payload", [{"error":"server failed"}, {}, {"data":None}])
def test_connectivity_rejects_invalid_model_listing(local_server, payload):
    from openprogram.models.listing.credentials import validate_credential
    _, requests = local_server
    requests.payload.clear()
    requests.payload.update(payload)
    result = validate_credential("ollama", use_cache=False, timeout=3)
    assert not result.ok


@pytest.mark.parametrize("payload", ["<html>Unavailable</html>", {"error":"model failed"}, {}])
def test_connectivity_rejects_invalid_inference_response(local_server, payload):
    from openprogram.models.listing.credentials import validate_credential
    _, requests = local_server
    requests.post_payload = payload
    result = validate_credential("ollama", model="local-test", use_cache=False, timeout=3)
    assert not result.ok


def test_vision_capability_and_configured_address_reach_public_stream(local_server, monkeypatch):
    import asyncio
    from openprogram.models.auth.resolver import ResolvedConnection
    from openprogram.models.providers.catalog.enabled_models import _build_model_from_row
    from openprogram.models.providers.settings.storage import _normalize_spec_row
    from openprogram.models.providers.streaming.stream import stream_simple
    from openprogram.models.providers.types import Context, ImageContent, SimpleStreamOptions, UserMessage
    conn = ResolvedConnection(kind="api_key", auth_value="local-account-key", base_url="http://127.0.0.1:1/v1", headers={})
    monkeypatch.setattr("openprogram.models.auth.usage.acquire_pooled", lambda *a: (conn, "default", "test-id"))
    monkeypatch.setattr("openprogram.models.auth.usage.record_call_success", lambda *a, **k: None)
    spec = _normalize_spec_row({"id":"local-test", "name":"Vision", "api":"openai-completions", "vision":True})
    model = _build_model_from_row(spec, "ollama", {"default":{"api":"openai-completions", "base_url":local_server[0]+"/v1"}})
    assert model.input == ["text", "image"]
    async def collect():
        return [event async for event in stream_simple(model, Context(messages=[UserMessage(content=[ImageContent(data="aGVsbG8=", mime_type="image/png")], timestamp=1)]), SimpleStreamOptions(max_tokens=8))]
    events = asyncio.run(collect())
    assert events[-1].type == "done", events[-1]
    _, headers, body = local_server[1][-1]
    assert headers["Authorization"] == "Bearer local-account-key"
    assert body["messages"][0]["content"][0]["type"] == "image_url"


def test_public_cloud_stream_still_requires_provider_credentials(local_server, monkeypatch):
    import asyncio
    from openprogram.models.providers.streaming.stream import stream_simple
    from openprogram.models.providers.types import Context, Model
    from openprogram.models.providers.utils.errors import ErrorReason, LLMError

    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-cloud-secret")
    monkeypatch.setattr("openprogram.models.auth.usage.stored_but_unusable", lambda *a: None)
    model = Model(id="cloud-test", name="Cloud", provider="openai", api="openai-completions", base_url=local_server[0]+"/v1")

    async def collect():
        return [event async for event in stream_simple(model, Context())]

    with pytest.raises(LLMError) as caught:
        asyncio.run(collect())
    assert caught.value.reason == ErrorReason.AUTHENTICATION
    assert caught.value.retryable is False
    assert not local_server[1]
