"""Tests for providers.openai_codex.auth_adapter.

These tests avoid real network calls by injecting a fake refresh via
register_provider_config (last registration wins). Integration with a
real auth.openai.com endpoint lives in tests/integration, not here.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.auth.manager import (
    AuthManager,
    get_provider_config,
    register_provider_config,
    ProviderAuthConfig,
)
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    Credential,
    CredentialPool,
    OAuthPayload,
)
from openprogram.providers.openai_codex import auth_adapter as adapter


@pytest.fixture
def store(tmp_path: Path):
    s = AuthStore(root=tmp_path / "store")
    set_store_for_testing(s)
    yield s
    set_store_for_testing(None)


def _jwt(exp_epoch: int, account_id: str = "acc_1") -> str:
    """Build a minimally-valid JWT with a specific exp and account claim."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(d).encode("ascii")
        ).rstrip(b"=").decode("ascii")
    header = b64({"alg": "none"})
    payload = b64({
        "exp": exp_epoch,
        adapter.JWT_CLAIM_PATH: {"chatgpt_account_id": account_id},
    })
    return f"{header}.{payload}.sig"


# ---- JWT helpers ---------------------------------------------------------

def test_jwt_expiry_epoch_ms_parses_exp():
    tok = _jwt(1_700_000_000)
    assert adapter.jwt_expiry_epoch_ms(tok) == 1_700_000_000 * 1000


def test_jwt_expiry_epoch_ms_bad_token_returns_none():
    assert adapter.jwt_expiry_epoch_ms("not.a.jwt") is None
    assert adapter.jwt_expiry_epoch_ms("only.two") is None


def test_extract_account_id_ok():
    tok = _jwt(1_700_000_000, account_id="acc_42")
    assert adapter.extract_account_id(tok) == "acc_42"


def test_extract_account_id_missing_raises():
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    body = base64.urlsafe_b64encode(b'{"exp": 1}').rstrip(b"=").decode("ascii")
    tok = f"{header}.{body}.sig"
    with pytest.raises(RuntimeError):
        adapter.extract_account_id(tok)


# ---- import_from_codex_file ----------------------------------------------

def test_import_from_codex_file_happy_path(tmp_path: Path):
    path = tmp_path / "auth.json"
    access = _jwt(int(time.time()) + 3600, account_id="acc_xyz")
    path.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": access,
            "refresh_token": "R-abc",
            "account_id": "acc_xyz",
        },
    }))
    cred = adapter.import_from_codex_file(auth_path=path)
    assert cred is not None
    assert cred.provider_id == adapter.PROVIDER_ID
    assert cred.kind == "oauth"
    assert cred.payload.access_token == access
    assert cred.payload.refresh_token == "R-abc"
    assert cred.payload.client_id == adapter.OAUTH_CLIENT_ID
    assert cred.payload.expires_at_ms > int(time.time() * 1000)
    assert cred.metadata["account_id"] == "acc_xyz"
    assert cred.read_only is False


def test_import_returns_none_when_missing(tmp_path):
    assert adapter.import_from_codex_file(auth_path=tmp_path / "nope.json") is None


def test_import_returns_none_on_corrupt(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json")
    assert adapter.import_from_codex_file(auth_path=path) is None


def test_import_returns_none_on_empty_tokens(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"tokens": {}}))
    assert adapter.import_from_codex_file(auth_path=path) is None


# ---- registration --------------------------------------------------------

def test_register_codex_auth_is_idempotent():
    # Adapter module has already registered on import. Re-registering
    # must not blow up, must preserve the refresh callable.
    adapter.register_codex_auth()
    cfg = get_provider_config(adapter.PROVIDER_ID)
    assert cfg.refresh is adapter._codex_refresh
    assert cfg.refresh_skew_seconds == 60


# ---- end-to-end with AuthManager + fake refresh --------------------------

def test_manager_refreshes_expired_codex_credential(store):
    # Set up: seed the store with an expired OAuth cred and register a
    # synthetic refresh that returns a fresh one, so we exercise the
    # whole manager path without a real HTTP call.
    expired = Credential(
        provider_id=adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token="old",
            refresh_token="R-old",
            expires_at_ms=int(time.time() * 1000) - 60_000,
            client_id=adapter.OAUTH_CLIENT_ID,
        ),
    )
    pool = CredentialPool(
        provider_id=adapter.PROVIDER_ID,
        profile_id="default",
        credentials=[expired],
    )
    store.put_pool(pool)

    def fake_refresh(cred):
        assert cred.payload.refresh_token == "R-old"
        return Credential(
            provider_id=cred.provider_id,
            profile_id=cred.profile_id,
            kind="oauth",
            payload=OAuthPayload(
                access_token="NEW-ACCESS",
                refresh_token="R-new",
                expires_at_ms=int(time.time() * 1000) + 3600_000,
                client_id=adapter.OAUTH_CLIENT_ID,
            ),
            credential_id=cred.credential_id,
        )

    register_provider_config(ProviderAuthConfig(
        provider_id=adapter.PROVIDER_ID,
        refresh_skew_seconds=60,
        refresh=fake_refresh,
    ))

    mgr = AuthManager(store=store)
    result = asyncio.run(mgr.acquire(adapter.PROVIDER_ID, "default"))
    assert result.payload.access_token == "NEW-ACCESS"
    # Verify persistence: disk copy is rotated too.
    persisted = store.find_pool(adapter.PROVIDER_ID, "default")
    assert persisted.credentials[0].payload.access_token == "NEW-ACCESS"

    # Re-register the real refresh so later tests are unaffected.
    adapter.register_codex_auth()


def test_acquire_sync_from_non_async_caller(store):
    # Seed with a fresh cred — no refresh needed, pure happy path.
    fresh = Credential(
        provider_id=adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token="A",
            refresh_token="R",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            client_id=adapter.OAUTH_CLIENT_ID,
        ),
    )
    store.put_pool(CredentialPool(
        provider_id=adapter.PROVIDER_ID,
        profile_id="default",
        credentials=[fresh],
    ))

    mgr = AuthManager(store=store)
    cred = mgr.acquire_sync(adapter.PROVIDER_ID, "default")
    assert cred.payload.access_token == "A"


def test_acquire_sync_rejects_running_loop(store):
    fresh = Credential(
        provider_id=adapter.PROVIDER_ID,
        profile_id="default",
        kind="oauth",
        payload=OAuthPayload(
            access_token="A", refresh_token="R",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            client_id=adapter.OAUTH_CLIENT_ID,
        ),
    )
    store.put_pool(CredentialPool(
        provider_id=adapter.PROVIDER_ID, profile_id="default",
        credentials=[fresh],
    ))
    mgr = AuthManager(store=store)

    async def inside():
        # We're inside a running loop now — acquire_sync must refuse.
        with pytest.raises(RuntimeError, match="running event loop"):
            mgr.acquire_sync(adapter.PROVIDER_ID, "default")

    asyncio.run(inside())


# ---- write-back to codex file -------------------------------------------

def test_write_back_updates_codex_file(tmp_path, monkeypatch):
    target = tmp_path / ".codex" / "auth.json"
    target.parent.mkdir()
    target.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "old", "refresh_token": "R-old"},
        "last_refresh": "2026-01-01T00:00:00.000Z",
    }))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    new_payload = OAuthPayload(
        access_token="NEW", refresh_token="R-NEW",
        expires_at_ms=int(time.time() * 1000) + 3600_000,
        client_id=adapter.OAUTH_CLIENT_ID,
        id_token="idtok",
    )
    adapter._write_back_to_codex_file(new_payload)

    data = json.loads(target.read_text())
    assert data["tokens"]["access_token"] == "NEW"
    assert data["tokens"]["refresh_token"] == "R-NEW"
    assert data["tokens"]["id_token"] == "idtok"
    assert data["auth_mode"] == "chatgpt"
    assert data["last_refresh"] != "2026-01-01T00:00:00.000Z"


def test_write_back_silent_when_path_unwritable(tmp_path, monkeypatch):
    # Point CODEX_HOME at a path where we can't create files (parent
    # doesn't exist and can't be made — simulated by using a file as parent).
    parent_is_file = tmp_path / "notadir"
    parent_is_file.write_text("hi")
    monkeypatch.setenv("CODEX_HOME", str(parent_is_file))

    new_payload = OAuthPayload(
        access_token="N", refresh_token="R",
        expires_at_ms=0, client_id=adapter.OAUTH_CLIENT_ID,
    )
    # Should NOT raise — write-back is best-effort.
    adapter._write_back_to_codex_file(new_payload)
