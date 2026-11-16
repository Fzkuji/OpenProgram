"""What a key check says beyond its status (models/listing/credentials.py,
key_check.py): the provider's own reason on a refusal, a key accepted whose
test message failed, a Responses provider asked the way it is used, and a
provider with no address here."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.listing import credentials as cr
from openprogram.models.listing import key_check as kc
from openprogram.models.providers.settings import storage as st


def _post(answer, calls=None):
    def fake(url, *, headers, json_body, timeout, configured_url, params=None):
        if calls is not None:
            calls.append((url, json_body))
        return answer
    return fake


def test_a_refused_key_carries_the_providers_reason_and_never_a_key():
    body = '{"error":{"message":"This API key has expired: sk-live-abcdef123456"}}'
    r = cr._interpret("opencode", "openai_bearer", (401, body, 5), via="GET /models")
    assert r.status == cr.INVALID_CREDENTIAL
    assert r.detail.startswith("Key rejected (HTTP 401). This API key has expired")
    assert "abcdef123456" not in r.detail
    plain = cr._interpret("opencode", "openai_bearer", (401, "<html>", 5), via="GET /models")
    assert plain.detail == "Key rejected (HTTP 401)."


def test_a_key_accepted_whose_test_message_fails_works(monkeypatch):
    monkeypatch.setattr(cr, "resolve_ping_model", lambda pid, **kw: "m1")
    monkeypatch.setattr(cr, "_layer2_post", _post((400, '{"error":{"message":"Unsupported parameter: max_tokens"}}', 9)))
    layer1 = cr._result("deepseek", cr.VALID, kind="openai_bearer", via="GET /models", http_status=200)
    r = cr._maybe_layer2_usable("deepseek", "openai_bearer", "k", "https://api.example/v1", layer1, 1)
    assert r.status == cr.VALID_MODEL_UNAVAILABLE
    assert r.detail.startswith("Key accepted; the test message failed: HTTP 400. Unsupported parameter")
    assert kc.result_of(r) == "works"


def test_a_refusal_on_the_test_message_is_still_a_refusal(monkeypatch):
    monkeypatch.setattr(cr, "resolve_ping_model", lambda pid, **kw: "m1")
    monkeypatch.setattr(cr, "_layer2_post", _post((401, "{}", 9)))
    layer1 = cr._result("deepseek", cr.VALID, kind="openai_bearer", via="GET /models", http_status=200)
    r = cr._maybe_layer2_usable("deepseek", "openai_bearer", "k", "https://api.example/v1", layer1, 1)
    assert r.status == cr.INVALID_CREDENTIAL


def test_a_responses_provider_is_pinged_on_responses(monkeypatch):
    calls: list = []
    monkeypatch.setattr(cr, "_provider_api", lambda pid: "openai-responses")
    monkeypatch.setattr(cr, "_layer2_post", _post((200, "{}", 3), calls))
    r = cr._layer2_ping("openai", "openai_bearer", "k", "https://api.example/v1", "gpt-x", 1)
    assert r.via == "POST /responses"
    url, body = calls[0]
    assert url == "https://api.example/v1/responses"
    assert body == {"model": "gpt-x", "input": "PING", "max_output_tokens": 16, "store": False}


def test_a_provider_with_no_address_here_is_not_configured(monkeypatch):
    monkeypatch.setattr(st, "_resolve_base_url", lambda pid: None)
    probe = SimpleNamespace(status=cr.UNKNOWN, detail=cr.NO_BASE_URL, http_status=None, model=None)
    assert kc.result_of(probe) == "not_configured"
    assert kc.provider_set_up("my-removed-custom") is False
    assert kc.provider_set_up("openai-codex") is True, "a sign-in provider needs no address"


def test_where_a_key_is_made_and_what_a_sign_in_is_called():
    from openprogram.models.auth.login.login_method_registry import provider_auth, signin_name
    assert provider_auth("opencode").keys_url == "https://opencode.ai/auth"
    assert provider_auth("fireworks-ai").keys_url == "https://fireworks.ai/account/api-keys"
    assert provider_auth("togetherai").keys_url == "https://api.together.ai/settings/api-keys"
    assert signin_name("openai-codex") == "ChatGPT"
    assert signin_name("google-gemini-cli") == "Gemini CLI"


def test_a_refusal_that_echoes_a_dotted_key_has_the_exact_key_cut_out(monkeypatch):
    key = "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOp"
    body = '{"error":{"message":"Invalid API key: ' + key + '"}}'
    monkeypatch.setattr(cr, "_http_get", lambda *a, **kw: (401, body, 5))
    r = cr.validate_credential("zai", api_key=key, base_url="https://api.example/v1", use_cache=False)
    assert r.status == cr.INVALID_CREDENTIAL
    assert "AbCdEfGhIjKlMnOp" not in r.detail and "0123456789abcdef" not in r.detail
    assert cr._KEY_IN_CHECK.get() == "", "the key is forgotten once the check ends"
