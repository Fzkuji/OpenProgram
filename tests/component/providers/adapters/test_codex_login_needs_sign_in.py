"""A delegated ChatGPT sign-in whose refresh token is spent is marked as
needing sign-in, is not asked again while the Codex CLI's file still holds
that token, and is taken back once the file holds another login.

The token endpoint is a fake that refuses every refresh token but the live
one, the way auth.openai.com does. Nothing reaches the network.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from openprogram.models.auth import credential_provider as cp
from openprogram.models.auth.credential_provider import CredentialProvider, get_provider_config
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import (
    AuthNeedsReauthError,
    Credential,
    CredentialData,
    CredentialPool,
)
from openprogram.models.providers.adapters.openai_codex import auth_adapter, credentials
from openprogram.models.providers.utils.errors import ErrorReason, classify_error

ACCOUNT = "acc_shared"


def _jwt(exp: int, iat: int, plan: str | None = None) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    auth = {"chatgpt_account_id": ACCOUNT}
    if plan:
        auth["chatgpt_plan_type"] = plan
    return f"{b64({'alg': 'none'})}.{b64({'exp': exp, 'iat': iat, auth_adapter.JWT_CLAIM_PATH: auth})}.sig"


class TokenServer:
    def __init__(self) -> None:
        self.live = "R-live"
        self.calls: list[str] = []
        self.error = "OAuth refresh failed 400 invalid_grant"

    def post_refresh(self, refresh_token: str) -> dict:
        self.calls.append(refresh_token)
        if refresh_token != self.live:
            raise RuntimeError(self.error)
        now = int(time.time())
        self.live = f"R-{len(self.calls)}"
        return {"access_token": _jwt(now + 3600, now), "refresh_token": self.live,
                "id_token": "id", "expires_in": 3600}


@pytest.fixture
def machine(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    tokens = TokenServer()
    monkeypatch.setattr(auth_adapter, "_post_refresh", tokens.post_refresh)
    auth_adapter.register_codex_auth()
    store = AuthStore(root=tmp_path / "auth")
    set_store_for_testing(store)
    manager = CredentialProvider(store=store)
    cp.set_credential_provider_for_testing(manager)
    path = codex_home / "auth.json"
    store.put_pool(CredentialPool(provider_id="openai-codex", account_id="default", credentials=[Credential(
        provider_id="openai-codex", account_id="default", kind="cli_delegated",
        payload=CredentialData(kind="cli_delegated", data={"store_path": str(path)}),
        source="codex_cli", read_only=True,
    )]))
    yield manager, path, tokens
    set_store_for_testing(None)
    cp.set_credential_provider_for_testing(None)
    auth_adapter.register_codex_auth()


def _login(path: Path, access: str, refresh: str, id_token: str = "") -> None:
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": access, "refresh_token": refresh, "id_token": id_token, "account_id": ACCOUNT}}))


def _stored(manager: CredentialProvider) -> Credential:
    return manager.store.find_pool("openai-codex", "default").credentials[0]


def _expired() -> str:
    return _jwt(int(time.time()) - 60, int(time.time()) - 3660)


def test_a_spent_refresh_token_marks_the_delegated_login_as_needing_sign_in(machine):
    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")

    with pytest.raises(AuthNeedsReauthError, match="codex login"):
        credentials.oauth_view(_stored(manager), manager)

    cred = _stored(manager)
    assert cred.status == "needs_reauth"
    assert "invalid_grant" in (cred.last_error or "")
    assert cred.metadata[credentials.SPENT_KEY] == auth_adapter.refresh_token_digest("R-spent")
    assert "R-spent" not in json.dumps(cred.metadata) + (cred.last_error or "")
    assert tokens.calls == ["R-spent"]


def test_a_marked_login_is_not_asked_again_while_the_file_is_unchanged(machine):
    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")
    with pytest.raises(AuthNeedsReauthError):
        credentials.oauth_view(_stored(manager), manager)

    for _ in range(3):
        with pytest.raises(AuthNeedsReauthError):
            credentials.oauth_view(_stored(manager), manager)
        with pytest.raises(AuthNeedsReauthError):
            credentials.refresh_delegated(_stored(manager), "rejected", manager)
    assert tokens.calls == ["R-spent"]


def test_the_next_request_says_to_sign_in_again_not_that_no_key_is_set(machine):
    import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
    import openprogram.models.providers.settings.env_api_keys as eak

    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")
    with pytest.raises(AuthNeedsReauthError):
        credentials.oauth_view(_stored(manager), manager)

    real = eak.resolve_provider_key
    eak.resolve_provider_key = lambda provider: None
    try:
        with pytest.raises(AuthNeedsReauthError, match="Sign in again"):
            cx._resolve_codex_bearer_token(None)
    finally:
        eak.resolve_provider_key = real
    assert tokens.calls == ["R-spent"]


def test_signing_in_again_with_the_codex_cli_takes_the_login_back(machine):
    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")
    with pytest.raises(AuthNeedsReauthError):
        credentials.oauth_view(_stored(manager), manager)

    fresh = _jwt(int(time.time()) + 3600, int(time.time()))
    _login(path, fresh, "R-new")
    assert credentials.acquire(manager, "default").payload.auth_value == fresh
    cred = _stored(manager)
    assert cred.status == "valid" and cred.last_error is None
    assert credentials.SPENT_KEY not in cred.metadata
    assert tokens.calls == ["R-spent"]


def test_a_login_the_codex_cli_refreshed_meanwhile_is_used_instead_of_marked(machine):
    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")
    fresh = _jwt(int(time.time()) + 3600, int(time.time()))

    def refused_after_the_cli_wrote(refresh_token: str) -> dict:
        _login(path, fresh, "R-cli")
        raise RuntimeError("OAuth refresh failed 400 refresh_token_reused")

    auth_adapter._post_refresh = refused_after_the_cli_wrote
    assert credentials.oauth_view(_stored(manager), manager).payload.auth_value == fresh
    assert _stored(manager).status != "needs_reauth"


def test_a_network_failure_leaves_the_login_unmarked(machine):
    manager, path, tokens = machine
    _login(path, _expired(), "R-spent")
    tokens.error = "connection reset"
    with pytest.raises(RuntimeError, match="connection reset"):
        credentials.oauth_view(_stored(manager), manager)
    assert _stored(manager).status != "needs_reauth"
    assert credentials.SPENT_KEY not in _stored(manager).metadata


def test_needing_sign_in_is_an_authentication_failure_no_retry_wins(machine):
    manager, path, _tokens = machine
    _login(path, _expired(), "R-spent")
    with pytest.raises(AuthNeedsReauthError) as caught:
        credentials.oauth_view(_stored(manager), manager)
    assert classify_error(caught.value) == (ErrorReason.AUTHENTICATION, False)


def test_refresh_rereads_the_login_refreshes_marks_and_takes_back(machine):
    manager, path, tokens = machine
    reread = get_provider_config("openai-codex").reread_login
    assert reread is not None

    # About to expire, refresh token still good: refreshed in the file.
    _login(path, _expired(), tokens.live)
    reread()
    assert json.loads(path.read_text())["tokens"]["refresh_token"] == tokens.live
    assert _stored(manager).status != "needs_reauth"

    # Spent: marked, and never raises out of Refresh.
    _login(path, _expired(), "R-spent")
    reread()
    assert _stored(manager).status == "needs_reauth"
    asked = len(tokens.calls)
    reread()
    assert len(tokens.calls) == asked

    # Signed in again: taken back.
    _login(path, _jwt(int(time.time()) + 3600, int(time.time())), "R-new")
    reread()
    assert _stored(manager).status == "valid"


def test_the_plan_comes_from_the_sign_in_and_is_none_when_it_names_none(machine):
    manager, path, _tokens = machine
    read_plan = get_provider_config("openai-codex").plan
    now = int(time.time())
    _login(path, _jwt(now + 3600, now), "R", id_token=_jwt(now + 3600, now, plan="Plus"))
    assert read_plan() == "plus"
    _login(path, _jwt(now + 3600, now, plan="pro"), "R")
    assert read_plan() == "pro"
    _login(path, _jwt(now + 3600, now), "R")
    assert read_plan() is None
    path.unlink()
    assert read_plan() is None
