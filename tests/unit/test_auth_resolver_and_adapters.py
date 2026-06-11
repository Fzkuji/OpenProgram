"""Tests for resolve_api_key_sync + Anthropic/Gemini/Copilot adapters."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.auth.context import auth_scope
from openprogram.auth.manager import AuthManager, set_manager_for_testing
from openprogram.auth.resolver import resolve_api_key_sync
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    ApiKeyPayload,
    Credential,
    CredentialPool,
    OAuthPayload,
)
from openprogram.providers.anthropic import auth_adapter as anthro_adapter
from openprogram.providers.github_copilot import auth_adapter as copilot_adapter
from openprogram.providers.google_gemini_cli import auth_adapter as gemini_adapter


@pytest.fixture
def store(tmp_path):
    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    # Reset the module-level AuthManager so its _store ref points at our fresh store.
    set_manager_for_testing(AuthManager(store=s))
    yield s
    set_store_for_testing(None)
    set_manager_for_testing(None)


# ---- resolver ------------------------------------------------------------

def test_resolver_uses_override_first(store):
    pinned = Credential(
        provider_id="openai", profile_id="default", kind="api_key",
        payload=ApiKeyPayload(api_key="sk-OVERRIDE"),
    )
    with auth_scope(credential_overrides={"openai": pinned}):
        assert resolve_api_key_sync("openai") == "sk-OVERRIDE"


def test_resolver_uses_store_when_no_override(store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cred = Credential(
        provider_id="openai", profile_id="default", kind="api_key",
        payload=ApiKeyPayload(api_key="sk-STORE"),
    )
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default", credentials=[cred],
    ))
    assert resolve_api_key_sync("openai") == "sk-STORE"


def test_resolver_falls_back_to_env(store, monkeypatch):
    # No store pool, no override — env var is the last resort.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ENV")
    assert resolve_api_key_sync("openai") == "sk-ENV"


def test_resolver_returns_none_when_nothing_matches(store, monkeypatch):
    for var in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    assert resolve_api_key_sync("anthropic") is None


def test_resolver_extracts_oauth_access_token(store):
    cred = Credential(
        provider_id="anthropic", profile_id="default", kind="oauth",
        payload=OAuthPayload(
            access_token="ACC", refresh_token="REF",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
        ),
    )
    store.put_pool(CredentialPool(
        provider_id="anthropic", profile_id="default", credentials=[cred],
    ))
    assert resolve_api_key_sync("anthropic") == "ACC"


def test_resolver_respects_active_profile(store):
    personal = Credential(
        provider_id="openai", profile_id="personal", kind="api_key",
        payload=ApiKeyPayload(api_key="sk-PERSONAL"),
    )
    work = Credential(
        provider_id="openai", profile_id="work", kind="api_key",
        payload=ApiKeyPayload(api_key="sk-WORK"),
    )
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="personal", credentials=[personal],
    ))
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="work", credentials=[work],
    ))

    with auth_scope(profile_id="work"):
        assert resolve_api_key_sync("openai") == "sk-WORK"
    with auth_scope(profile_id="personal"):
        assert resolve_api_key_sync("openai") == "sk-PERSONAL"


# ---- Anthropic adapter ---------------------------------------------------

def test_anthropic_import_from_claude_code(tmp_path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat-ABC",
            "refreshToken": "REF",
            "expiresAt": 1712345678901,
            "subscriptionType": "pro",
            "scopes": ["user:inference"],
        },
    }))
    cred = anthro_adapter.import_from_claude_code(path=path)
    assert cred is not None
    assert cred.kind == "cli_delegated"
    assert cred.read_only is True
    assert cred.metadata["subscription_type"] == "pro"
    assert cred.metadata["scopes"] == ["user:inference"]
    assert cred.payload.access_key_path == ["claudeAiOauth", "accessToken"]


def test_anthropic_import_returns_none_when_file_missing(tmp_path):
    assert anthro_adapter.import_from_claude_code(path=tmp_path / "nope.json") is None


def test_anthropic_import_api_key_wrapper():
    cred = anthro_adapter.import_api_key("sk-ant-api03-XYZ")
    assert cred.kind == "api_key"
    assert cred.payload.api_key == "sk-ant-api03-XYZ"
    assert cred.read_only is False
    assert cred.metadata["imported_from"] == "paste"


def test_anthropic_register_is_idempotent():
    anthro_adapter.register_anthropic_auth()
    anthro_adapter.register_anthropic_auth()  # should not raise


# ---- Gemini CLI adapter --------------------------------------------------

def test_gemini_import_happy_path(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({
        "access_token": "ya29.XXX",
        "refresh_token": "1//YYY",
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "token_type": "Bearer",
        "expiry_date": 1712345678901,
    }))
    cred = gemini_adapter.import_from_gemini_cli(path=path)
    assert cred is not None
    assert cred.kind == "cli_delegated"
    assert cred.read_only is True
    assert cred.metadata["scope"].startswith("https://")


def test_gemini_import_corrupt(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text("{not json")
    assert gemini_adapter.import_from_gemini_cli(path=path) is None


# ---- Copilot adapter -----------------------------------------------------

def test_copilot_prefers_copilot_token_over_gh(monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghu_COPILOT")
    monkeypatch.setenv("GH_TOKEN", "ghu_GH")
    cred = copilot_adapter.import_from_env_tokens()
    assert cred is not None
    assert cred.payload.api_key == "ghu_COPILOT"
    assert cred.metadata["env_var"] == "COPILOT_GITHUB_TOKEN"


def test_copilot_falls_through_to_github_token(monkeypatch):
    for var in ["COPILOT_GITHUB_TOKEN", "GH_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_LAST")
    cred = copilot_adapter.import_from_env_tokens()
    assert cred is not None
    assert cred.payload.api_key == "ghp_LAST"


def test_copilot_returns_none_when_none_set(monkeypatch):
    for var in ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    assert copilot_adapter.import_from_env_tokens() is None


def test_copilot_oauth_wrapper():
    cred = copilot_adapter.import_oauth_credential("ACC", "REF", expires_at_ms=1)
    assert cred.kind == "oauth"
    assert cred.payload.access_token == "ACC"
    assert cred.payload.client_id == "Iv1.b507a08c87ecfe98"
