"""Claude subscription login — browser PKCE + setup-token paste.

The claude-code / anthropic providers gain a browser OAuth login (manual
``code#state`` paste against claude.ai) and a setup-token paste, both
landing in the anthropic AuthStore pool. These tests pin the wiring; the
live token exchange is verified out-of-band (params confirmed against the
real endpoint).
"""
from __future__ import annotations

import asyncio

import pytest


# --- OAuth config -----------------------------------------------------------

def test_build_pkce_config_shape():
    from openprogram.providers.anthropic import auth_adapter as a
    cfg = a.build_pkce_config()
    assert cfg.authorize_url == "https://claude.ai/oauth/authorize"
    assert cfg.token_url == "https://console.anthropic.com/v1/oauth/token"
    assert cfg.client_id == a.OAUTH_CLIENT_ID
    assert cfg.manual_paste_only is True
    assert cfg.redirect_uri_override == "https://console.anthropic.com/oauth/code/callback"
    assert cfg.token_use_json is True
    assert cfg.extra_authorize_params.get("code") == "true"


# --- setup-token paste ------------------------------------------------------

def test_import_setup_token_lands_oauth_in_anthropic_pool():
    from openprogram.providers.anthropic import auth_adapter as a
    cred = a.import_setup_token("  sk-ant-oat-FAKE  ", profile_id="work")
    assert cred.provider_id == "anthropic"   # not claude-code
    assert cred.profile_id == "work"
    assert cred.kind == "oauth"
    assert cred.payload.access_token == "sk-ant-oat-FAKE"   # trimmed
    assert cred.payload.refresh_token == ""                  # setup-token has none
    assert cred.read_only is False


# --- refresh ----------------------------------------------------------------

def test_refresh_is_noop_without_refresh_token():
    from openprogram.providers.anthropic import auth_adapter as a
    cred = a.import_setup_token("sk-ant-oat-FAKE")
    same = a._anthropic_refresh(cred)
    assert same is cred  # handed back unchanged, no network call


def test_refresh_posts_refresh_token(monkeypatch):
    from openprogram.providers.anthropic import auth_adapter as a
    from openprogram.auth.types import Credential, OAuthPayload

    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"access_token": "sk-ant-oat-NEW", "refresh_token": "rt-NEW", "expires_in": 28800}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)

    cred = Credential(
        provider_id="anthropic", profile_id="default", kind="oauth",
        payload=OAuthPayload(
            access_token="sk-ant-oat-OLD", refresh_token="rt-OLD",
            expires_at_ms=0, scope=a.OAUTH_SCOPES,
            client_id=a.OAUTH_CLIENT_ID, token_endpoint=a.OAUTH_TOKEN_URL,
        ),
        source="pkce_oauth:anthropic",
    )
    new = a._anthropic_refresh(cred)
    assert captured["url"] == a.OAUTH_TOKEN_URL
    assert captured["body"]["grant_type"] == "refresh_token"
    assert captured["body"]["refresh_token"] == "rt-OLD"
    assert new.payload.access_token == "sk-ant-oat-NEW"
    assert new.payload.refresh_token == "rt-NEW"
    assert new.credential_id == cred.credential_id  # same row


# --- login_methods + driver -------------------------------------------------

@pytest.mark.parametrize("provider", ["anthropic", "claude-code"])
def test_claude_login_methods(provider):
    from openprogram.auth.login_methods import login_methods, default_method
    ids = [m[0] for m in login_methods(provider)]
    assert ids == ["pkce_oauth", "setup_token"]
    assert default_method(provider) == "pkce_oauth"


def test_claude_code_credential_pool_is_anthropic():
    from openprogram.auth.login_driver import _credential_provider_id
    assert _credential_provider_id("claude-code") == "anthropic"
    assert _credential_provider_id("anthropic") == "anthropic"
    assert _credential_provider_id("openai-codex") == "openai-codex"


@pytest.mark.parametrize("provider", ["anthropic", "claude-code"])
def test_pkce_config_dispatch(provider):
    from openprogram.auth.login_driver import _pkce_config
    cfg = _pkce_config(provider)
    assert cfg.client_id.startswith("9d1c250a")


def test_setup_token_method_stores_under_anthropic():
    """run_login(setup_token) on claude-code stores in the anthropic pool."""
    from openprogram.auth.login_driver import run_login

    class _Ui:
        async def open_url(self, url): ...
        async def show_progress(self, m): ...
        async def show_code(self, *a, **k): ...
        async def prompt(self, message, *, secret=False):
            return "sk-ant-oat-PASTED"

    cred = asyncio.run(run_login("claude-code", "acct2", "setup_token", _Ui()))
    assert cred.provider_id == "anthropic"
    assert cred.profile_id == "acct2"
    assert cred.payload.access_token == "sk-ant-oat-PASTED"


# --- PKCE framework: manual-paste mode --------------------------------------

def test_pkce_manual_paste_flow(monkeypatch):
    """manual_paste_only: no callback server, prompt for code#state, JSON exchange."""
    from openprogram.auth.methods import pkce_oauth as p
    from openprogram.auth.methods.pkce_oauth import PkceLoginMethod, PkceTokens

    captured = {}

    async def _fake_exchange(*, cfg, code, verifier, redirect_uri, state=""):
        captured["code"] = code
        captured["state"] = state
        captured["redirect_uri"] = redirect_uri
        return PkceTokens(access_token="sk-ant-oat-X", refresh_token="rt", expires_in=28800)

    monkeypatch.setattr(p, "_exchange_code_for_tokens", _fake_exchange)

    opened = {}

    class _Ui:
        async def open_url(self, url): opened["url"] = url
        async def show_progress(self, m): ...
        async def show_code(self, *a, **k): ...
        async def prompt(self, message, *, secret=False):
            # user pastes code#state from the hosted page
            return "THECODE#THESTATE"

    from openprogram.providers.anthropic import auth_adapter as a
    cfg = a.build_pkce_config()  # real config (carries code=true)
    cred = asyncio.run(PkceLoginMethod("anthropic", cfg, profile_id="default").run(_Ui()))
    assert captured["code"] == "THECODE"  # split on '#'
    assert captured["redirect_uri"] == "https://console.anthropic.com/oauth/code/callback"
    assert cred.payload.access_token == "sk-ant-oat-X"
    assert "code=true" in opened["url"]
