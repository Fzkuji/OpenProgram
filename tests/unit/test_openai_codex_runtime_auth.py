"""End-to-end test that OpenAICodexRuntime uses AuthManager correctly.

These tests don't construct a real Runtime (that needs the openai-codex
provider registry + HTTP client setup); instead they exercise the
narrow auth-acquisition helpers in ``runtime.py`` that were the point
of the v2 migration. If _ensure_credential / _account_id_for work,
Runtime.__init__ and Runtime.exec are mechanical plumbing over them.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from openprogram.auth.manager import (
    AuthManager,
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    AuthConfigError,
    Credential,
    CredentialPool,
    OAuthPayload,
)
from openprogram.providers.openai_codex import auth_adapter
from openprogram.providers.openai_codex.runtime import (
    _account_id_for,
    _ensure_credential,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Fresh store pointed at tmp_path and CODEX_HOME redirected so
    import_from_codex_file doesn't touch the developer's real file."""
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    yield store, tmp_path
    set_store_for_testing(None)
    # Restore real refresh fn so later tests see the right config.
    auth_adapter.register_codex_auth()


def _jwt(exp_epoch: int, account_id: str = "acc_e2e") -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(d).encode("ascii")
        ).rstrip(b"=").decode("ascii")
    return (
        f"{b64({'alg':'none'})}."
        f"{b64({'exp': exp_epoch, auth_adapter.JWT_CLAIM_PATH: {'chatgpt_account_id': account_id}})}"
        ".sig"
    )


def test_ensure_credential_imports_from_codex_file_when_pool_absent(isolated):
    store, tmp = isolated
    # Seed ~/.codex/auth.json (via CODEX_HOME override)
    auth_path = tmp / ".codex" / "auth.json"
    auth_path.parent.mkdir()
    access = _jwt(int(time.time()) + 3600, account_id="acc_fromfile")
    auth_path.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": access,
            "refresh_token": "R-abc",
            "account_id": "acc_fromfile",
        },
    }))
    mgr = AuthManager(store=store)

    cred = _ensure_credential(mgr, "default")
    assert cred.payload.access_token == access
    assert cred.metadata["account_id"] == "acc_fromfile"
    # The import should have created a pool, so a second call returns
    # the same thing without re-importing.
    assert store.find_pool(auth_adapter.PROVIDER_ID, "default") is not None
    cred2 = _ensure_credential(mgr, "default")
    assert cred2.credential_id == cred.credential_id


def test_ensure_credential_raises_when_no_pool_and_no_file(isolated):
    store, tmp = isolated
    mgr = AuthManager(store=store)
    # ~/.codex/auth.json does not exist — import path returns None,
    # _ensure_credential should surface a config error.
    with pytest.raises(AuthConfigError, match="codex login"):
        _ensure_credential(mgr, "default")


def test_ensure_credential_triggers_refresh_when_jwt_expired(isolated):
    store, tmp = isolated
    # Seed pool with an expired credential.
    expired_access = _jwt(int(time.time()) - 60, account_id="acc_exp")
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token=expired_access,
            refresh_token="R-old",
            expires_at_ms=int(time.time() * 1000) - 60_000,
            client_id=auth_adapter.OAUTH_CLIENT_ID,
        ),
    )
    store.put_pool(CredentialPool(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        credentials=[cred],
    ))

    # Swap in a fake refresh to avoid the network.
    new_access = _jwt(int(time.time()) + 3600, account_id="acc_new")

    def fake_refresh(c):
        return Credential(
            provider_id=c.provider_id,
            profile_id=c.profile_id,
            kind="oauth",
            payload=OAuthPayload(
                access_token=new_access,
                refresh_token="R-new",
                expires_at_ms=int(time.time() * 1000) + 3600_000,
                client_id=auth_adapter.OAUTH_CLIENT_ID,
            ),
            metadata=dict(c.metadata),
            credential_id=c.credential_id,
        )

    register_provider_config(ProviderAuthConfig(
        provider_id=auth_adapter.PROVIDER_ID,
        refresh_skew_seconds=60,
        refresh=fake_refresh,
    ))

    mgr = AuthManager(store=store)
    result = _ensure_credential(mgr, "default")
    assert result.payload.access_token == new_access


def test_account_id_prefers_metadata_over_jwt():
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token=_jwt(int(time.time()) + 60, account_id="acc_from_jwt"),
            refresh_token="R",
            expires_at_ms=int(time.time() * 1000) + 60_000,
            client_id=auth_adapter.OAUTH_CLIENT_ID,
        ),
        metadata={"account_id": "acc_from_metadata"},
    )
    assert _account_id_for(cred) == "acc_from_metadata"


def test_account_id_falls_back_to_jwt():
    cred = Credential(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token=_jwt(int(time.time()) + 60, account_id="acc_jwt_only"),
            refresh_token="R",
            expires_at_ms=int(time.time() * 1000) + 60_000,
            client_id=auth_adapter.OAUTH_CLIENT_ID,
        ),
    )
    assert _account_id_for(cred) == "acc_jwt_only"


def test_profile_isolation(isolated):
    """Two profiles each with their own credential — acquire returns each."""
    store, tmp = isolated

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
                    client_id=auth_adapter.OAUTH_CLIENT_ID,
                ),
            )],
        ))

    seed("personal", _jwt(int(time.time()) + 3600, account_id="acc_personal"))
    seed("work", _jwt(int(time.time()) + 3600, account_id="acc_work"))

    mgr = AuthManager(store=store)
    c_personal = _ensure_credential(mgr, "personal")
    c_work = _ensure_credential(mgr, "work")
    assert _account_id_for(c_personal) == "acc_personal"
    assert _account_id_for(c_work) == "acc_work"
