"""One ChatGPT sign-in, shared by every holder on the machine, is refreshed once
and the refreshed token is the one every later request sends first.

A fake token endpoint rotates refresh tokens the way auth.openai.com does
(a spent refresh token is refused), and a fake Codex backend accepts only the
access tokens that endpoint issued last. Nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
import openprogram.models.providers.settings.env_api_keys as eak
from openprogram.models.auth import credential_provider as cp
from openprogram.models.auth.credential_provider import CredentialProvider
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth import selection
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.models.providers.adapters.openai_codex import auth_adapter
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, UserMessage

ACCOUNT = "acc_shared"


def _jwt(exp: int, iat: int, account: str = ACCOUNT) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    claims = {"exp": exp, "iat": iat, auth_adapter.JWT_CLAIM_PATH: {"chatgpt_account_id": account}}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


class TokenServer:
    """auth.openai.com: each refresh token is good for one refresh."""

    def __init__(self) -> None:
        self.serial = 0
        self.live_refresh = "R0"
        self.calls: list[str] = []
        self.issued: list[str] = []

    def post_refresh(self, refresh_token: str) -> dict:
        self.calls.append(refresh_token)
        if refresh_token != self.live_refresh:
            raise RuntimeError("OAuth refresh failed 400")
        self.serial += 1
        now = int(time.time()) + self.serial  # strictly later issue times
        access = _jwt(now + 3600, now)
        self.live_refresh = f"R{self.serial}"
        self.issued.append(access)
        return {"access_token": access, "refresh_token": self.live_refresh,
                "id_token": "id", "expires_in": 3600}


class _Response:
    def __init__(self, status: int, lines: list[str]) -> None:
        self.status_code = status
        self.headers: dict = {}
        self._lines = lines

    async def aread(self) -> bytes:
        return b'{"error": {"code": "token_expired"}}'

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class Backend:
    """chatgpt.com/backend-api: accepts only the newest issued access token."""

    def __init__(self, tokens: TokenServer) -> None:
        self.tokens = tokens
        self.sent: list[str] = []

    @contextlib.asynccontextmanager
    async def _stream(self, method, url, headers=None, content=None):
        bearer = headers["Authorization"].removeprefix("Bearer ")
        self.sent.append(bearer)
        if not self.tokens.issued or bearer != self.tokens.issued[-1]:
            yield _Response(401, [])
            return
        done = {"type": "response.completed", "response": {
            "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}
        yield _Response(200, [f"data: {json.dumps(done)}", "data: [DONE]"])

    def stream(self, method, url, headers=None, content=None):
        return self._stream(method, url, headers=headers, content=content)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A Codex CLI file, a store, the fake endpoints and their wiring."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    tokens = TokenServer()
    backend = Backend(tokens)
    monkeypatch.setattr(auth_adapter, "_post_refresh", tokens.post_refresh)
    monkeypatch.setattr(cx, "get_shared_async_client", lambda *a, **k: backend)
    monkeypatch.setattr(cx, "build_async_client", lambda *a, **k: backend)
    monkeypatch.setattr(cx, "validate_input_modalities", lambda model, context: None)
    monkeypatch.setattr(eak, "resolve_provider_key", lambda provider: None)
    auth_adapter.register_codex_auth()
    yield tmp_path, codex_home / "auth.json", tokens, backend
    set_store_for_testing(None)
    cp.set_credential_provider_for_testing(None)
    auth_adapter.register_codex_auth()


def _use_store(root: Path) -> CredentialProvider:
    store = AuthStore(root=root)
    set_store_for_testing(store)
    manager = CredentialProvider(store=store)
    cp.set_credential_provider_for_testing(manager)
    return manager


def _write_codex_file(path: Path, access: str, refresh: str) -> None:
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": access, "refresh_token": refresh, "account_id": ACCOUNT}}))


def _delegated(path: Path) -> Credential:
    return Credential(
        provider_id="openai-codex", kind="import",
        payload=CredentialData(kind="import", data={"store_path": str(path)}),
        source="codex_cli", read_only=True,
    )


def _oauth(access: str, refresh: str) -> Credential:
    return Credential(
        provider_id="openai-codex", kind="oauth",
        payload=CredentialData(kind="oauth", auth_value=access, data={
            "refresh_token": refresh,
            "expires_at_ms": auth_adapter.jwt_expiry_epoch_ms(access),
            "client_id": auth_adapter.OAUTH_CLIENT_ID,
        }),
    )


def _stored(root: Path) -> Credential:
    return AuthStore(root=root).get("openai-codex").credentials[0]


_MODEL = dict(id="gpt-test", name="test", api="openai-codex", provider="openai-codex",
              base_url="https://chatgpt.com/backend-api")


async def _send() -> str:
    """One request through the credential layer and the Codex adapter."""
    context = Context(system_prompt="sys", messages=[UserMessage(content="hi", timestamp=0)])
    model = Model(**_MODEL)
    final = None
    async for event in selection.with_credential(
            model, SimpleStreamOptions(),
            lambda opts: cx.stream_simple_openai_codex_responses(model, context, opts)):
        if getattr(event, "type", None) == "done":
            final = event.message
    return final.stop_reason if final is not None else "none"


def _request() -> str:
    """One request; its stop reason."""
    return asyncio.run(_send())


def test_a_rejected_delegated_login_is_refreshed_once_and_the_next_request_sends_the_new_token(machine):
    # The development instance's case: its login is the Codex CLI's file, and
    # the backend rejects that token although its own exp is days away.
    tmp, path, tokens, backend = machine
    revoked = _jwt(int(time.time()) + 4 * 86400, int(time.time()) - 86400)
    _write_codex_file(path, revoked, "R0")
    manager = _use_store(tmp / "dev")
    manager.store.add_credential(_delegated(path))

    assert _request() == "stop"
    assert tokens.calls == ["R0"]
    fresh = tokens.issued[-1]
    assert backend.sent == [revoked, fresh]
    persisted = json.loads(path.read_text())["tokens"]
    assert persisted["access_token"] == fresh and persisted["refresh_token"] == "R1"

    assert _request() == "stop"
    assert backend.sent[2:] == [fresh]  # first attempt, no 401
    assert tokens.calls == ["R0"]


def test_a_delegated_login_past_its_exp_is_refreshed_before_the_request(machine):
    tmp, path, tokens, backend = machine
    expired = _jwt(int(time.time()) - 60, int(time.time()) - 3660)
    _write_codex_file(path, expired, "R0")
    manager = _use_store(tmp / "dev")
    manager.store.add_credential(_delegated(path))

    assert _request() == "stop"
    assert backend.sent == [tokens.issued[-1]]
    assert tokens.calls == ["R0"]


def test_an_expired_stored_login_is_refreshed_persisted_and_sent_first(machine):
    tmp, path, tokens, backend = machine
    expired = _jwt(int(time.time()) - 60, int(time.time()) - 3660)
    _write_codex_file(path, expired, "R0")
    manager = _use_store(tmp / "stable")
    manager.store.add_credential(_oauth(expired, "R0"))

    assert _request() == "stop"
    fresh = tokens.issued[-1]
    assert backend.sent == [fresh]
    stored = _stored(tmp / "stable")
    assert stored.payload.auth_value == fresh and stored.payload.data["refresh_token"] == "R1"
    assert json.loads(path.read_text())["tokens"]["access_token"] == fresh

    assert _request() == "stop"
    assert backend.sent == [fresh, fresh]
    assert tokens.calls == ["R0"]


def test_an_instance_adopts_the_login_another_instance_refreshed_instead_of_spending_a_used_token(machine):
    # Stable holds a stored copy of the family the development instance reads
    # through the Codex file. After dev refreshes, stable's copy carries a
    # spent refresh token; it must take the file's newer login, not refresh.
    tmp, path, tokens, backend = machine
    revoked = _jwt(int(time.time()) + 4 * 86400, int(time.time()) - 86400)
    _write_codex_file(path, revoked, "R0")

    dev = _use_store(tmp / "dev")
    dev.store.add_credential(_delegated(path))
    assert _request() == "stop"
    fresh = tokens.issued[-1]

    stable = _use_store(tmp / "stable")
    stable.store.add_credential(_oauth(revoked, "R0"))
    assert _request() == "stop"
    assert backend.sent == [revoked, fresh, fresh]
    assert tokens.calls == ["R0"]  # R0 was never spent twice
    stored = _stored(tmp / "stable")
    assert stored.payload.auth_value == fresh and stored.status == "ready"


_DELEGATED_CHILD = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    from openprogram.models.providers.adapters.openai_codex import auth_adapter
    path, log, rejected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

    def post(refresh_token):
        with log.open("a") as f:
            f.write(refresh_token + "\\n")
        time.sleep(0.5)
        return {"access_token": "NEW-ACCESS", "refresh_token": "R1", "expires_in": 3600}

    auth_adapter._post_refresh = post
    print(auth_adapter.refresh_shared_login(path, rejected))
""")

_STORED_CHILD = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from openprogram.models.auth.credential_provider import (
        CredentialProvider, ProviderAuthConfig, register_provider_config)
    from openprogram.models.auth.store import AuthStore, set_store_for_testing
    from openprogram.models.auth.types import Credential, CredentialData
    root, log = Path(sys.argv[1]), Path(sys.argv[2])

    def refresh(cred):
        with log.open("a") as f:
            f.write(cred.payload.data["refresh_token"] + "\\n")
        time.sleep(0.5)
        return Credential(
            provider_id=cred.provider_id, kind="oauth", id=cred.id,
            payload=CredentialData(kind="oauth", auth_value="NEW-ACCESS", data={
                "refresh_token": "R1", "expires_at_ms": int(time.time() * 1000) + 3600_000}))

    register_provider_config(ProviderAuthConfig(provider_id="openai-codex", refresh=refresh))
    store = AuthStore(root=root)
    set_store_for_testing(store)
    manager = CredentialProvider(store=store)
    print(manager.acquire_sync("openai-codex").payload.auth_value)
""")


def _race(script: str, *args: str) -> list[str]:
    children = [subprocess.Popen([sys.executable, "-c", script, *args],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    outputs = []
    for child in children:
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, err
        outputs.append(out.strip().splitlines()[-1])
    return outputs


def test_two_processes_refreshing_one_delegated_login_spend_its_refresh_token_once(tmp_path):
    path = tmp_path / "auth.json"
    revoked = _jwt(int(time.time()) + 3600, int(time.time()) - 60)
    _write_codex_file(path, revoked, "R0")
    log = tmp_path / "refreshes.log"

    assert _race(_DELEGATED_CHILD, str(path), str(log), revoked) == ["NEW-ACCESS", "NEW-ACCESS"]
    assert log.read_text().split() == ["R0"]


def test_two_processes_refreshing_one_stored_login_spend_its_refresh_token_once(tmp_path):
    root = tmp_path / "store"
    AuthStore(root=root).add_credential(Credential(
        provider_id="openai-codex", kind="oauth",
        payload=CredentialData(kind="oauth", auth_value="OLD", data={
            "refresh_token": "R0", "expires_at_ms": int(time.time() * 1000) - 60_000})))
    log = tmp_path / "refreshes.log"

    assert _race(_STORED_CHILD, str(root), str(log)) == ["NEW-ACCESS", "NEW-ACCESS"]
    assert log.read_text().split() == ["R0"]


def test_a_refreshed_login_is_written_owner_only_and_never_reaches_the_log(machine, caplog):
    # The Codex file is the only copy of a delegated login's rotated refresh
    # token: it is saved readable by its owner alone, and the INFO line that
    # reports the refresh names the file, never a token.
    tmp, path, tokens, backend = machine
    revoked = _jwt(int(time.time()) + 4 * 86400, int(time.time()) - 86400)
    _write_codex_file(path, revoked, "R0")
    path.chmod(0o600)
    manager = _use_store(tmp / "dev")
    manager.store.add_credential(_delegated(path))

    with caplog.at_level("DEBUG"):
        assert _request() == "stop"
    assert path.stat().st_mode & 0o077 == 0
    logged = caplog.text
    for secret in (revoked, tokens.issued[-1], "R0", "R1"):
        assert secret not in logged


def test_the_token_is_resolved_off_the_event_loop(machine, monkeypatch):
    # Making the login ready may wait on another process's refresh lock and
    # spend a refresh token over HTTPS; the Worker's loop keeps running.
    tmp, path, tokens, backend = machine
    import threading
    loop_thread: list[int] = []
    resolved_on: list[int] = []
    fresh = _jwt(int(time.time()) + 3600, int(time.time()))
    tokens.issued.append(fresh)
    _write_codex_file(path, fresh, "R0")
    manager = _use_store(tmp / "dev")
    manager.store.add_credential(_delegated(path))
    real = manager.prepare

    def prepare(cred):
        resolved_on.append(threading.get_ident())
        return real(cred)

    monkeypatch.setattr(manager, "prepare", prepare)

    async def run():
        loop_thread.append(threading.get_ident())
        return await _send()

    assert asyncio.run(run()) == "stop"
    assert backend.sent == [fresh]
    assert resolved_on and loop_thread[0] not in resolved_on


def test_an_adopted_login_never_overwrites_a_newer_one_saved_meanwhile(machine):
    # A request read the Codex file's login while a refresh saved a newer one
    # to the store; saving the older adoption must not put back a login whose
    # refresh token was already spent.
    from openprogram.models.providers.adapters.openai_codex import credentials

    tmp, path, tokens, backend = machine
    now = int(time.time())
    old, adopted, newest = (_jwt(now + 3600, now - 20), _jwt(now + 3600, now - 10),
                            _jwt(now + 3600, now))
    manager = _use_store(tmp / "stable")
    stale_view = manager.store.add_credential(_oauth(old, "R0"))

    def _newer(row: Credential) -> None:
        row.payload = _oauth(newest, "R2").payload

    manager.store.update("openai-codex", stale_view.id, _newer)
    _write_codex_file(path, adopted, "R1")

    credentials.oauth_view(stale_view, manager)
    stored = _stored(tmp / "stable")
    assert stored.payload.auth_value == newest and stored.payload.data["refresh_token"] == "R2"
