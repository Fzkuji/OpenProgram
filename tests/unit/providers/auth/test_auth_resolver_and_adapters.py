"""Tests for resolve_api_key_sync + Anthropic/Gemini/Copilot adapters."""
from __future__ import annotations

import json
import time

import pytest

from openprogram.models.auth.credential_provider import CredentialProvider, set_credential_provider_for_testing
from openprogram.models.auth.resolver import resolve_api_key_sync
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.models.providers.adapters.anthropic import auth_adapter as anthro_adapter
from openprogram.models.providers.adapters.github_copilot import auth_adapter as copilot_adapter
from openprogram.models.providers.adapters.google_gemini_cli import auth_adapter as gemini_adapter


@pytest.fixture
def store(tmp_path):
    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    # Reset the module-level CredentialProvider so its _store ref points at our fresh store.
    set_credential_provider_for_testing(CredentialProvider(store=s))
    yield s
    set_store_for_testing(None)
    set_credential_provider_for_testing(None)


# ---- resolver ------------------------------------------------------------

def _add(store, provider, kind, value, **data):
    return store.add_credential(Credential(
        provider_id=provider, kind=kind,
        payload=CredentialData(kind=kind, auth_value=value, data=data)))


def test_resolver_uses_the_first_key_of_the_list(store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _add(store, "openai", "api_key", "sk-FIRST")
    _add(store, "openai", "api_key", "sk-SECOND")
    assert resolve_api_key_sync("openai") == "sk-FIRST"


def test_resolver_skips_a_turned_off_key(store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    first = _add(store, "openai", "api_key", "sk-FIRST")
    _add(store, "openai", "api_key", "sk-SECOND")
    store.update("openai", first.id, lambda row: setattr(row, "enabled", False))
    assert resolve_api_key_sync("openai") == "sk-SECOND"


def test_resolver_reads_a_set_variable_as_a_key(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ENV")
    assert resolve_api_key_sync("openai") == "sk-ENV"


def test_resolver_returns_none_when_nothing_matches(store, monkeypatch):
    for var in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    assert resolve_api_key_sync("anthropic") is None


def test_resolver_rejects_anthropic_subscription_token(store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _add(store, "anthropic", "api_key", "sk-ant-oat01-subscription")
    assert resolve_api_key_sync("anthropic") is None


# ---- Anthropic adapter ---------------------------------------------------

def test_anthropic_import_api_key_wrapper():
    cred = anthro_adapter.import_api_key("sk-ant-api03-XYZ")
    assert cred.kind == "api_key"
    assert cred.payload.auth_value == "sk-ant-api03-XYZ"
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
    assert cred.kind == "import"
    assert cred.read_only is True
    assert cred.metadata["scope"].startswith("https://")


def test_gemini_import_corrupt(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text("{not json")
    assert gemini_adapter.import_from_gemini_cli(path=path) is None


# ---- Copilot adapter -----------------------------------------------------

def test_copilot_oauth_wrapper():
    cred = copilot_adapter.import_oauth_credential("ACC", "REF", expires_at_ms=1)
    assert cred.kind == "oauth"
    assert cred.payload.auth_value == "ACC"
    assert cred.payload.data["client_id"] == "Iv1.b507a08c87ecfe98"


def test_copilot_reads_its_token_variables_in_order(store, monkeypatch):
    for var in ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"]:
        monkeypatch.delenv(var, raising=False)
    assert resolve_api_key_sync("github-copilot") is None
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-general")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghu-copilot")
    rows = store.get("github-copilot").credentials
    assert [c.id for c in rows] == ["env:COPILOT_GITHUB_TOKEN", "env:GITHUB_TOKEN"]
