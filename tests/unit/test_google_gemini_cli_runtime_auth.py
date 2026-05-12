"""Auth-acquisition tests for GeminiCLIRuntime.

Mirrors tests/unit/test_openai_codex_runtime_auth.py — we exercise the
narrow auth helpers without instantiating a full Runtime (which needs a
live HTTP stack). The Gemini CLI's OAuth file is redirected to tmp_path
so import_from_gemini_cli never reads the developer's real file.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.auth.manager import AuthManager
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    AuthConfigError,
    CliDelegatedPayload,
    Credential,
    CredentialPool,
    OAuthPayload,
)
from openprogram.providers.google_gemini_cli import auth_adapter
from openprogram.providers.google_gemini_cli.runtime import (
    _access_token_for,
    _ensure_credential,
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
    mgr = AuthManager(store=store)

    cred = _ensure_credential(mgr, "default")
    assert cred.kind == "cli_delegated"
    assert _access_token_for(cred) == "ya29.fromfile"
    # Pool created — a second acquire returns the same credential without
    # re-importing the file.
    assert store.find_pool(auth_adapter.PROVIDER_ID, "default") is not None
    cred2 = _ensure_credential(mgr, "default")
    assert cred2.credential_id == cred.credential_id


def test_ensure_credential_raises_when_no_pool_and_no_file(isolated):
    store, *_ = isolated
    mgr = AuthManager(store=store)
    with pytest.raises(AuthConfigError, match="gemini auth login"):
        _ensure_credential(mgr, "default")


def test_access_token_reads_live_file_for_cli_delegated(isolated):
    """cli_delegated credentials point at the CLI's on-disk JSON, so a
    token rotation on disk must propagate without us re-importing."""
    _store, _tmp, creds_path = isolated
    _write_creds(creds_path, access="ya29.original")
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        kind="cli_delegated",
        payload=CliDelegatedPayload(
            store_path=str(creds_path),
            access_key_path=["access_token"],
            refresh_key_path=["refresh_token"],
            expires_key_path=["expiry_date"],
        ),
    )
    assert _access_token_for(cred) == "ya29.original"

    # Simulate the gemini CLI rotating the token on disk.
    _write_creds(creds_path, access="ya29.rotated")
    assert _access_token_for(cred) == "ya29.rotated"


def test_access_token_reads_oauth_payload_directly():
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token="ya29.oauth_direct",
            refresh_token="1//ref",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            client_id="fake",
        ),
    )
    assert _access_token_for(cred) == "ya29.oauth_direct"


def test_profile_isolation(isolated):
    store, _tmp, creds_path = isolated

    def seed(profile_id: str, token: str) -> None:
        store.put_pool(CredentialPool(
            provider_id=auth_adapter.PROVIDER_ID,
            profile_id=profile_id,
            credentials=[Credential(
                provider_id=auth_adapter.PROVIDER_ID,
                profile_id=profile_id,
                kind="oauth",
                payload=OAuthPayload(
                    access_token=token, refresh_token="R",
                    expires_at_ms=int(time.time() * 1000) + 3600_000,
                    client_id="fake",
                ),
            )],
        ))

    seed("personal", "ya29.personal")
    seed("work", "ya29.work")
    mgr = AuthManager(store=store)
    assert _access_token_for(_ensure_credential(mgr, "personal")) == "ya29.personal"
    assert _access_token_for(_ensure_credential(mgr, "work")) == "ya29.work"
