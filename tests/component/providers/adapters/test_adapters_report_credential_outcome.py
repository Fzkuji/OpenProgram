"""Every adapter hands a provider's refusal to the credential layer instead of
retrying it itself: one request per key, the limited key cools down and the
next key is tried (models/providers/streaming/credential_failover.py, streaming/stream.py)."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openprogram.models.auth import selection
from openprogram.models.providers.streaming import credential_failover
from openprogram.models.auth.credential_provider import (
    CredentialProvider, set_credential_provider_for_testing,
)
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.models.providers.types import (
    Context, Model, SimpleStreamOptions, UserMessage,
)

P = "outcome-test"


class _Refuser(BaseHTTPRequestHandler):
    keys: list[str] = []
    status = 429

    def do_POST(self):  # noqa: N802 - http.server API
        self.rfile.read(int(self.headers.get("content-length") or 0))
        key = (self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")
               or (self.headers.get("authorization") or "").removeprefix("Bearer ")
               or self.path.split("key=")[-1])
        type(self).keys.append(key)
        body = json.dumps({"error": {"message": "Rate limit reached", "type": "rate_limit_error",
                                     "code": 429, "status": "RESOURCE_EXHAUSTED"}}).encode()
        self.send_response(type(self).status)
        self.send_header("content-type", "application/json")
        self.send_header("retry-after", "120")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Refuser.keys = []
    _Refuser.status = 429
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Refuser)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def store(tmp_path):
    value = AuthStore(root=tmp_path / "state")
    set_store_for_testing(value)
    set_credential_provider_for_testing(CredentialProvider(store=value))
    selection.forget_bindings()
    for name in ("first", "second"):
        value.add_credential(Credential(
            provider_id=P, kind="api_key", label=name,
            payload=CredentialData(kind="api_key", auth_value=f"sk-{name}-0000000000")))
    yield value
    set_store_for_testing(None)
    set_credential_provider_for_testing(None)


APIS = {
    "openai-completions": "{base}/v1",
    "openai-responses": "{base}/v1",
    "anthropic-messages": "{base}",
    "google-generative-ai": "{base}",
}


def _send(api: str, server: str) -> None:
    from openprogram.models.providers.catalog.register import register_builtins
    from openprogram.models.providers.streaming.stream import stream_simple
    register_builtins()
    model = Model(id="test-model", name="test-model", api=api, provider=P,
                  base_url=APIS[api].format(base=server), max_tokens=64)
    context = Context(messages=[UserMessage(content="hi", timestamp=0)])

    async def go():
        async for _ in stream_simple(model, context, SimpleStreamOptions()):
            pass

    with pytest.raises(Exception):
        asyncio.run(go())


@pytest.mark.parametrize("api", sorted(APIS))
def test_a_limited_key_gives_way_to_the_next_after_one_request(api, server, store):
    _send(api, server)
    assert _Refuser.keys == ["sk-first-0000000000", "sk-second-0000000000"], \
        f"{api}: one request per key, no retry of its own"
    for cred in store.get(P).credentials:
        assert cred.status == "cooling", (api, cred.label, cred.last_error)


@pytest.mark.parametrize("api", sorted(APIS))
def test_a_server_error_is_sent_again_twice_on_the_same_key(api, server, store, monkeypatch):
    async def no_wait(_s):
        return None

    monkeypatch.setattr(credential_failover.asyncio, "sleep", no_wait)
    _Refuser.status = 503
    _send(api, server)
    assert _Refuser.keys == ["sk-first-0000000000"] * 3, api
    assert all(c.status == "ready" for c in store.get(P).credentials)
