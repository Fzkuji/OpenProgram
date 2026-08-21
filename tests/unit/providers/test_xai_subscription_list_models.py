"""Grok Subscription live model list (CLI chat proxy, not api.x.ai)."""
from __future__ import annotations

from openprogram.webui._model_listing import fetchers as F
from openprogram.providers.xai_subscription import list_models as X


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
            return {"data": [{"id": "grok-4.5"}, {"id": "grok-4"}]}

    class _Client:
        def get(self, url, headers=None, timeout=None):
            calls["url"] = url
            calls["headers"] = headers or {}
            return _Resp()

    monkeypatch.setattr(
        "openprogram.security.safe_http.safe_client",
        lambda *_a, **_k: _ClientContext(_Client()),
    )
    out = X.fetch("xai-subscription", 5.0)
    assert [m["id"] for m in out] == ["grok-4.5", "grok-4"]
    assert calls["url"] == "https://cli-chat-proxy.grok.com/v1/models"
    assert calls["headers"].get("X-XAI-Token-Auth") == "xai-grok-cli"
    assert calls["headers"].get("Authorization") == "Bearer tok_abc"


def test_fetch_without_token_errors(monkeypatch):
    monkeypatch.setattr(X, "_token", lambda pid: "")
    out = X.fetch("xai-subscription", 5.0)
    assert isinstance(out, dict) and "not signed in" in out["error"]
