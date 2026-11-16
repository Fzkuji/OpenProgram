"""Auth-acquisition tests for GeminiCLIRuntime.

Mirrors tests/unit/providers/adapters/openai_codex/test_openai_codex_runtime_auth.py — we exercise the
narrow auth helpers without instantiating a full Runtime (which needs a
live HTTP stack). The Gemini CLI's OAuth file is redirected to tmp_path
so import_from_gemini_cli never reads the developer's real file.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.models.auth.credential_provider import CredentialProvider
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import AuthConfigError, Credential, CredentialData
from openprogram.models.providers.adapters.google_gemini_cli import auth_adapter
from openprogram.models.providers.adapters.google_gemini_cli.credentials import (
    access_token_for,
    ensure_credential,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Fresh store + redirected gemini_cli_credentials_path."""
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    creds_path = tmp_path / ".gemini" / "oauth_creds.json"
    monkeypatch.setattr(
        auth_adapter, "gemini_cli_credentials_path", lambda: creds_path
    )
    yield store, tmp_path, creds_path
    set_store_for_testing(None)


def _write_creds(path: Path, access: str = "ya29.fake", refresh: str = "1//ref") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "access_token": access,
        "refresh_token": refresh,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "token_type": "Bearer",
        "expiry_date": int((time.time() + 3600) * 1000),
    }))


def test_ensure_credential_imports_from_gemini_cli_file_when_pool_absent(isolated):
    store, _tmp, creds_path = isolated
    _write_creds(creds_path, access="ya29.fromfile")
    mgr = CredentialProvider(store=store)

    cred = ensure_credential(mgr)
    assert cred.kind == "import"
    assert access_token_for(cred) == "ya29.fromfile"
    # Pool created — a second acquire returns the same credential without
    # re-importing the file.
    assert len(store.get(auth_adapter.PROVIDER_ID).credentials) == 1
    cred2 = ensure_credential(mgr)
    assert cred2.id == cred.id


def test_ensure_credential_raises_when_no_pool_and_no_file(isolated):
    store, *_ = isolated
    mgr = CredentialProvider(store=store)
    with pytest.raises(AuthConfigError, match="gemini auth login"):
        ensure_credential(mgr)


def test_access_token_reads_live_file_for_an_imported_login(isolated):
    """An imported login points at the CLI's on-disk JSON, so a
    token rotation on disk must propagate without us re-importing."""
    _store, _tmp, creds_path = isolated
    _write_creds(creds_path, access="ya29.original")
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        kind="import",
        payload=CredentialData(
            kind="import",
            data={
                "store_path": str(creds_path),
                "access_key_path": ["access_token"],
                "refresh_key_path": ["refresh_token"],
                "expires_key_path": ["expiry_date"],
            },
        ),
    )
    assert access_token_for(cred) == "ya29.original"

    # Simulate the gemini CLI rotating the token on disk.
    _write_creds(creds_path, access="ya29.rotated")
    assert access_token_for(cred) == "ya29.rotated"


def test_access_token_reads_oauth_payload_directly():
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        kind="oauth",
        payload=CredentialData(
            kind="oauth", auth_value="ya29.oauth_direct",
            data={
                "refresh_token": "1//ref",
                "expires_at_ms": int(time.time() * 1000) + 3600_000,
                "client_id": "fake",
            },
        ),
    )
    assert access_token_for(cred) == "ya29.oauth_direct"


def test_the_first_enabled_login_is_used(isolated):
    store, _tmp, creds_path = isolated

    def seed(token: str) -> Credential:
        return store.add_credential(Credential(
            provider_id=auth_adapter.PROVIDER_ID,
            kind="oauth",
            payload=CredentialData(
                kind="oauth", auth_value=token,
                data={
                    "refresh_token": "R",
                    "expires_at_ms": int(time.time() * 1000) + 3600_000,
                    "client_id": "fake",
                },
            ),
        ))

    personal = seed("ya29.personal")
    seed("ya29.work")
    mgr = CredentialProvider(store=store)
    assert access_token_for(ensure_credential(mgr)) == "ya29.personal"
    store.update(auth_adapter.PROVIDER_ID, personal.id, lambda c: setattr(c, "enabled", False))
    assert access_token_for(ensure_credential(mgr)) == "ya29.work"
