"""Grok Subscription live model list (CLI chat proxy, not api.x.ai)."""
from __future__ import annotations

from openprogram.models.listing import fetchers as F
from openprogram.models.providers.adapters.xai_subscription import list_models as X


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *_args):
        return False


def test_load_fetcher_finds_xai_subscription():
    fn = F._load_fetcher("xai-subscription")
    assert fn is X.fetch


def test_fetch_hits_cli_proxy_with_grok_headers(monkeypatch):
    monkeypatch.setattr(X, "_token", lambda pid: "tok_abc")
    calls = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{
                "id": "grok-4.6", "name": "Grok 4.6",
                "context_window": 500_000,
                "supports_reasoning_effort": True,
                "reasoning_effort": "high",
                "reasoning_efforts": [
                    {"id": "xhigh", "default": False},
                    {"id": "high", "default": True},
                    {"id": "medium", "default": False},
                    {"id": "low", "default": False},
                ],
                "api_backend": "responses",
                "supports_backend_search": True,
            }, {"id": "future-grok"}]}

    class _Client:
        def get(self, url, headers=None, timeout=None):
            calls["url"] = url
            calls["headers"] = headers or {}
            return _Resp()

    monkeypatch.setattr(
        "openprogram.host.security.safe_http.safe_client",
        lambda *_a, **_k: _ClientContext(_Client()),
    )
    out = X.fetch("xai-subscription", 5.0)
    assert [m["id"] for m in out] == ["grok-4.6", "future-grok"]
    assert out[0]["context_window"] == 500_000
    assert out[0]["thinking_levels"] == ["xhigh", "high", "medium", "low"]
    assert out[0]["default_thinking_level"] == "high"
    assert out[0]["api_backend"] == "responses"
    assert "max_tokens" not in out[0]
    assert out[1] == {"id": "future-grok", "name": "future-grok"}
    assert calls["url"] == "https://cli-chat-proxy.grok.com/v1/models"
    assert calls["headers"].get("X-XAI-Token-Auth") == "xai-grok-cli"
    assert calls["headers"].get("Authorization") == "Bearer tok_abc"


def test_grok_build_route_preserves_catalog_ids():
    from openprogram.models.providers.adapters.xai_subscription.headers import grok_build_route, grok_cli_headers

    assert grok_build_route("grok-4.6") == "grok-4.6"
    assert grok_build_route("grok-4.5") == "grok-4.5"
    assert grok_build_route("grok-build") == "grok-build"
    assert grok_build_route("") == "grok-build"
    headers = grok_cli_headers("grok-4.6")
    assert headers["x-grok-model-override"] == "grok-4.6"
    assert headers["x-grok-client-identifier"] == "grok-shell"


def test_fetch_without_token_errors(monkeypatch):
    monkeypatch.setattr(X, "_token", lambda pid: "")
    out = X.fetch("xai-subscription", 5.0)
    assert isinstance(out, dict) and "not signed in" in out["error"]


def test_responses_client_routes_to_the_selected_subscription_model(monkeypatch):
    import importlib
    from types import SimpleNamespace
    import openai

    adapter = importlib.import_module(
        "openprogram.models.providers.adapters.openai_responses.openai_responses"
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        "openprogram.models.providers.utils.http_client.get_shared_async_client",
        lambda *args, **kwargs: object(),
    )
    result = adapter._create_client(
        SimpleNamespace(provider="xai-subscription", id="grok-4.6", headers={}),
        SimpleNamespace(messages=[]), "test-credential",
    )
    assert result["base_url"] == "https://cli-chat-proxy.grok.com/v1"
    assert result["default_headers"]["x-grok-model-override"] == "grok-4.6"
