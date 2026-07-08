"""Unit tests for the unified credential validator.

``openprogram/webui/_model_listing/credentials.py`` is the single entry point
for "is this provider key valid?" across every surface (save-key verify, the
connectivity button, the batch status rows). It maps HTTP status codes to a
closed status enum per provider KIND, with a 60s cache and a layer-1 (auth-only)
vs layer-2 (named-model inference ping) split. A status->outcome regression
here ships silently everywhere, so the mapping is pinned by these tests.

No network: the layer-1 probe (``_http_get``) and the layer-2 ping
(``httpx.post``) are monkeypatched, and key/base resolution is stubbed.
"""
from __future__ import annotations

import pytest

from openprogram.webui._model_listing import credentials as cr
from openprogram.webui._model_listing import storage as st


# pure classifiers

@pytest.mark.parametrize("pid,kind", [
    ("openrouter", "openrouter_key"),
    ("anthropic", "anthropic_native"),
    ("google", "google_query"),
    ("openai-codex", "oauth"),
    ("gemini-subscription", "oauth"),
    ("amazon-bedrock", "cloud"),
    ("azure-openai-responses", "cloud"),
    ("deepseek", "openai_bearer"),
    ("openai", "openai_bearer"),
    ("groq", "openai_bearer"),
    # Third-party Anthropic-wire provider: probed Anthropic-style against
    # its own base_url, not as openai_bearer (which 404s on its host).
    ("minimax-cn", "anthropic_compat"),
])
def test_kind_for(pid, kind):
    assert cr._kind_for(pid) == kind


@pytest.mark.parametrize("status,body,expected", [
    (429, "", True),
    (503, "", True),
    (500, "", True),
    (504, "", True),
    (404, "no endpoints available", True),
    (404, "your data policy blocks this", True),
    (404, "NO ENDPOINTS", True),          # body is lowercased -> uppercase ok
    (404, "model not found", False),
    (200, "", False),
    (401, "", False),
])
def test_is_model_unavailable(status, body, expected):
    assert cr._is_model_unavailable(status, body) is expected


@pytest.mark.parametrize("status,body,expected", [
    (402, "", True),
    (200, "insufficient_quota", True),
    (200, "Insufficient Balance", True),
    (200, "you have exceeded your current quota", True),
    (200, "all good", False),
    (401, "", False),
])
def test_is_no_balance(status, body, expected):
    assert cr._is_no_balance(status, body) is expected


@pytest.mark.parametrize("body,expected", [
    ('{"data":{"limit_remaining":0}}', True),
    ('{"data":{"limit_remaining":0.0}}', True),
    ('{"data":{"limit_remaining":5}}', False),
    ('{"data":{"limit_remaining":null}}', False),
    ('{"data":{}}', False),
    ("not json", False),
])
def test_openrouter_exhausted(body, expected):
    assert cr._openrouter_exhausted(body) is expected


@pytest.mark.parametrize("env,pid", [
    ("OPENROUTER_API_KEY", "openrouter"),
    ("DEEPSEEK_API_KEY", "deepseek"),
    ("GOOGLE_API_KEY", "google"),
    ("GEMINI_API_KEY", "google"),
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("BRAVE_API_KEY", None),          # non-LLM search key -> skip validation
    ("TOTALLY_UNKNOWN_KEY", None),
])
def test_provider_id_for_env_var(env, pid):
    assert cr.provider_id_for_env_var(env) == pid


# validate_credential — layer 1 (auth-only), per outcome

@pytest.fixture
def stub_base(monkeypatch):
    """openai_bearer / openrouter need a base URL; stub it so no config is read."""
    monkeypatch.setattr(st, "_resolve_base_url", lambda pid: "https://api.example/v1")


def _patch_http_get(monkeypatch, result):
    calls = {"n": 0}

    def fake(url, *, headers=None, params=None, timeout=15.0):
        calls["n"] += 1
        return result

    monkeypatch.setattr(cr, "_http_get", fake)
    return calls


@pytest.mark.parametrize("result,expected_status", [
    ((200, "[]", 12), cr.VALID),
    ((401, '{"error":"bad key"}', 12), cr.INVALID_CREDENTIAL),
    ((403, "forbidden", 12), cr.INVALID_CREDENTIAL),
    ((402, "payment required", 12), cr.VALID_NO_BALANCE),
    ((200, "insufficient_quota", 12), cr.VALID),  # body-quota only triggers on non-200
    ((429, "slow down", 12), cr.UNKNOWN),          # auth endpoint rate-limited = inconclusive
    ((404, "weird", 12), cr.UNKNOWN),
    (None, cr.UNKNOWN),                              # transport error
])
def test_validate_layer1_openai_bearer(monkeypatch, stub_base, result, expected_status):
    _patch_http_get(monkeypatch, result)
    r = cr.validate_credential("deepseek", api_key="k", use_cache=False)
    assert r.status == expected_status
    assert r.kind == "openai_bearer"
    if expected_status == cr.VALID:
        assert r.ok and r.via == "GET /models"


def test_validate_anthropic_compat_probes_own_host_v1_models(monkeypatch):
    # minimax-cn (api='anthropic-messages') must probe its OWN base_url with
    # the Anthropic GET /v1/models + x-api-key, NOT the openai_bearer
    # GET /models (which 404s on api.minimaxi.com/anthropic and would brand
    # a valid key invalid_credential).
    monkeypatch.setattr(
        st, "_resolve_base_url", lambda pid: "https://api.minimaxi.com/anthropic",
    )
    seen = {}

    def fake(url, *, headers=None, params=None, timeout=15.0):
        seen["url"] = url
        seen["headers"] = headers or {}
        return (200, '{"data":[{"id":"MiniMax-M2.5"}]}', 11)

    monkeypatch.setattr(cr, "_http_get", fake)
    r = cr.validate_credential("minimax-cn", api_key="k", use_cache=False)
    assert r.status == cr.VALID and r.ok and r.kind == "anthropic_compat"
    assert r.via == "GET /v1/models"
    assert seen["url"] == "https://api.minimaxi.com/anthropic/v1/models"
    assert seen["headers"].get("x-api-key") == "k"
    assert "anthropic-version" in seen["headers"]


def test_validate_anthropic_compat_rejects_bad_key(monkeypatch):
    monkeypatch.setattr(
        st, "_resolve_base_url", lambda pid: "https://api.minimaxi.com/anthropic",
    )
    monkeypatch.setattr(
        cr, "_http_get",
        lambda url, **kw: (401, '{"error":{"message":"invalid api key"}}', 9),
    )
    r = cr.validate_credential("minimax-cn", api_key="bad", use_cache=False)
    assert r.status == cr.INVALID_CREDENTIAL and not r.ok


def test_validate_openrouter_uses_key_endpoint_and_balance(monkeypatch, stub_base):
    # OpenRouter /models is public, so the probe must hit /key; a 0 remaining
    # limit in the body is reported as valid_no_balance.
    _patch_http_get(monkeypatch, (200, '{"data":{"limit_remaining":0}}', 9))
    r = cr.validate_credential("openrouter", api_key="k", use_cache=False)
    assert r.status == cr.VALID_NO_BALANCE and r.via == "GET /key"

    _patch_http_get(monkeypatch, (200, '{"data":{"limit_remaining":12.5}}', 9))
    r = cr.validate_credential("openrouter", api_key="k", use_cache=False)
    assert r.status == cr.VALID


def test_validate_missing_key(monkeypatch):
    monkeypatch.setattr(st, "_resolve_api_key", lambda pid: None)
    r = cr.validate_credential("deepseek", use_cache=False)
    assert r.status == cr.MISSING and not r.ok


def test_validate_cloud_not_applicable():
    r = cr.validate_credential("amazon-bedrock", use_cache=False)
    assert r.status == cr.NOT_APPLICABLE and r.kind == "cloud"


def test_cache_hit_avoids_second_probe(monkeypatch, stub_base):
    calls = _patch_http_get(monkeypatch, (200, "[]", 5))
    r1 = cr.validate_credential("deepseek", api_key="k", use_cache=True)
    r2 = cr.validate_credential("deepseek", api_key="k", use_cache=True)
    assert r1.status == cr.VALID and r2.status == cr.VALID
    assert r2.cached is True
    assert calls["n"] == 1  # second call served from the 60s cache
    cr._cache.clear()


# validate_credential — layer 2 (named model -> inference ping)

class _FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def test_validate_layer2_model_unavailable(monkeypatch):
    import httpx
    monkeypatch.setattr(st, "_resolve_base_url", lambda pid: "https://openrouter.ai/api/v1")
    monkeypatch.setattr(
        httpx, "post",
        lambda url, **kw: _FakeResp(503, '{"error":{"message":"no healthy upstream"}}'),
    )
    r = cr.validate_credential("openrouter", model="x/y:free", api_key="k", use_cache=False)
    # key authenticated, that one model is just down right now
    assert r.status == cr.VALID_MODEL_UNAVAILABLE and r.ok and r.model == "x/y:free"


def test_validate_layer2_ok(monkeypatch):
    import httpx
    monkeypatch.setattr(st, "_resolve_base_url", lambda pid: "https://api.example/v1")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResp(200, '{"choices":[]}'))
    r = cr.validate_credential("deepseek", model="deepseek-chat", api_key="k", use_cache=False)
    assert r.status == cr.VALID and r.model == "deepseek-chat"


def test_validate_legacy_shape(monkeypatch, stub_base):
    _patch_http_get(monkeypatch, (200, "[]", 7))
    d = cr.validate_credential("deepseek", api_key="k", use_cache=False).to_legacy()
    assert d["ok"] is True and d["via"] == "GET /models" and "error" not in d
