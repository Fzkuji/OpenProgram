"""The provider credential routes (routes/identity/provider_credentials.py)
and the auth router's discovery, doctor and event stream
(routes/identity/credentials.py). A minimal FastAPI app with just these
routes; no secret ever leaves a response."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.auth import selection
from openprogram.models.auth.credential_provider import CredentialProvider, set_credential_provider_for_testing
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import (
    AuthEvent,
    AuthEventType,
    Credential,
    CredentialData,
)
from openprogram.interfaces.server.routes.identity import credentials as auth_routes
from openprogram.interfaces.server.routes.identity import provider_credentials, provider_login
from openprogram.models.listing import credentials as cr
from openprogram.interfaces.server.routes.identity.credentials import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client over an isolated store; key checks always accept."""
    from openprogram.models.listing import credentials
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    set_credential_provider_for_testing(CredentialProvider(store=store))
    selection.forget_bindings()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "validate_credential", lambda *a, **k: _result(cr.VALID))
    from openprogram.state import config as user_config
    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    # A checked key reads its balance and lists its models; never over the network here.
    from openprogram.models.listing import key_check
    monkeypatch.setattr(key_check, "read_new_balance", lambda provider, account: None)
    monkeypatch.setattr(key_check, "suggested_models", lambda provider, rows=None: (0, []))

    # Reset the SSE wire-up so each test gets a fresh listener on our new store.
    auth_routes._wired = False
    auth_routes._subscribers.clear()

    app = FastAPI()
    app.include_router(router)
    provider_credentials.register(app)
    provider_login.register(app)
    with TestClient(app) as c:
        yield c, store

    set_store_for_testing(None)
    set_credential_provider_for_testing(None)
    provider_login._SESSIONS.clear()


def _result(status, *, kind="openai_bearer", via="GET /models", detail=None):
    ok = status == cr.VALID
    payload = {"status": status, "ok": ok, "kind": kind, "via": via, "detail": detail}
    return SimpleNamespace(status=status, ok=ok, kind=kind, via=via, detail=detail,
                           to_dict=lambda: dict(payload))


def _read_cfg(provider):
    from openprogram.models.providers.settings.storage import _read_providers_cfg
    return _read_providers_cfg().get(provider) or {}


def _key(store, label, value, provider="openai"):
    return store.add_credential(Credential(
        provider_id=provider, kind="api_key", label=label,
        payload=CredentialData(kind="api_key", auth_value=value))).id


ROW_FIELDS = {"id", "label", "display_label", "kind", "source", "identity", "key_tail", "env_var",
              "read_only", "enabled", "status", "cooldown_until", "blocked_models", "last_error",
              "last_used_at", "use_count", "created_at", "today", "limits", "signin_name"}


# ---- the list -----------------------------------------------------------

def test_an_empty_provider_lists_its_ways_to_connect(client):
    c, _ = client
    body = c.get("/api/providers/openai/credentials").json()
    assert set(body) == {"provider", "strategy", "strategies", "active", "auth", "credentials", "set_up"}
    assert body["set_up"] is True
    assert (body["provider"], body["credentials"], body["active"]) == ("openai", [], None)
    assert body["strategies"] == ["in_order", "evenly", "least_used"]
    assert [m["id"] for m in body["auth"]["methods"]] == ["api_key"]


def test_added_keys_list_in_order_without_their_secrets(client):
    c, store = client
    first = c.post("/api/providers/openai/credentials",
                   json={"api_key": "sk-first-secret-1111", "label": "Work"}).json()
    assert first["ok"] is True and first["check"] is not None
    c.post("/api/providers/openai/credentials",
           json={"api_key": "sk-second-secret-2222", "validate": False})
    r = c.get("/api/providers/openai/credentials")
    rows = r.json()["credentials"]
    assert [row["label"] for row in rows] == ["Work", ""]
    assert [row["display_label"] for row in rows] == ["Work", "Key ····2222"]
    assert set(rows[0]) == ROW_FIELDS
    assert rows[0]["status"] == "ready" and rows[0]["source"] == "web"
    assert r.json()["active"] == rows[0]["id"]
    assert "secret" not in r.text


def test_the_same_key_twice_is_one_row(client):
    c, _ = client
    for _ in range(2):
        c.post("/api/providers/openai/credentials", json={"api_key": "sk-same-0000001", "validate": False})
    assert len(c.get("/api/providers/openai/credentials").json()["credentials"]) == 1


@pytest.mark.parametrize("body", [None, {}, {"api_key": "sk-x-000001", "name": "w"},
                                  {"api_key": "sk-x-000001", "validate": "no"},
                                  {"api_key": "sk-ant-oat01-abc"}])
def test_an_add_with_a_bad_body_is_400(client, body):
    c, store = client
    assert c.post("/api/providers/openai/credentials", json=body).status_code == 400
    assert store.get("openai").credentials == []


def test_a_set_environment_key_is_a_read_only_row(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-00009999")
    r = c.get("/api/providers/openai/credentials")
    row = r.json()["credentials"][0]
    assert (row["id"], row["source"], row["env_var"], row["read_only"], row["key_tail"]) == \
        ("env:OPENAI_API_KEY", "env", "OPENAI_API_KEY", True, "9999")
    assert "sk-from-env" not in r.text
    gone = c.delete("/api/providers/openai/credentials/env:OPENAI_API_KEY")
    assert gone.status_code == 409 and gone.json()["env_var"] == "OPENAI_API_KEY"


# ---- the key check on add -----------------------------------------------

def test_a_working_key_turns_a_new_provider_on(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: _result(cr.VALID, kind="openrouter_key", via="GET /key"))
    r = c.post("/api/providers/openrouter/credentials", json={"api_key": "sk-or-secret-0001"})
    check = r.json()["check"]
    assert r.status_code == 200 and check["result"] == "works"
    assert check["auto_enabled"] is True and _read_cfg("openrouter").get("enabled") is True
    assert "sk-or-secret" not in r.text


def test_a_key_without_credit_is_kept_out_of_turn(client, monkeypatch):
    c, store = client
    monkeypatch.setattr(cr, "validate_credential",
                        lambda *a, **k: _result(cr.VALID_NO_BALANCE, detail="no credits"))
    r = c.post("/api/providers/deepseek/credentials", json={"api_key": "sk-ds-secret-0001"})
    assert r.status_code == 200 and r.json()["check"]["result"] == "no_credit"
    assert r.json()["credential"]["status"] == "quota"
    assert store.get("deepseek").credentials[0].status == "quota"


def test_a_rejected_key_answers_its_check_and_is_not_kept(client, monkeypatch):
    c, store = client
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: _result(cr.INVALID_CREDENTIAL))
    r = c.post("/api/providers/openai/credentials", json={"api_key": "sk-bad-secret-0001"})
    assert r.status_code == 400
    assert r.json()["check"]["result"] == "rejected"
    assert r.json()["check"]["fix_url"] == "https://platform.openai.com/api-keys"
    assert store.get("openai").credentials == []
    assert not _read_cfg("openai").get("enabled")


# ---- sign-in ------------------------------------------------------------

@pytest.mark.parametrize("body", [None, [], {"method": 1}, {"label": 1}, {"label": "x" * 121},
                                  {"account": "work"}, {"extra": True}])
def test_a_sign_in_with_a_bad_start_body_is_400(client, body):
    c, _ = client
    assert c.post("/api/providers/openai-codex/login/start", json=body).status_code == 400


def test_a_sign_in_adds_a_row_and_signing_in_again_renews_it(client, monkeypatch):
    from openprogram.models.auth.login import login_driver
    from openprogram.models.listing import fetch_models_remote  # noqa: F401
    import openprogram.models.listing as listing
    c, store = client
    monkeypatch.setattr(listing, "fetch_models_remote", lambda name: None)

    async def fake_login(provider, method, ui, *, api_key=None, label="", source="web"):
        return Credential(provider_id=provider, kind="oauth", label=label, source=source,
                          identity={"email": "me@example.com"},
                          payload=CredentialData(kind="oauth", auth_value=f"access-{label or 'again'}"))

    monkeypatch.setattr(login_driver, "run_login", fake_login)

    def sign_in(label):
        start = c.post("/api/providers/openai-codex/login/start", json={"method": "pkce_oauth", "label": label})
        assert start.status_code == 200
        for _ in range(50):
            poll = c.get("/api/providers/openai-codex/login/poll", params={"session": start.json()["session"]}).json()
            if poll["done"]:
                return poll
        raise AssertionError("sign-in did not finish")

    first = sign_in("Personal 工作")
    assert first["ok"] is True and first["label"] == "Personal 工作"
    again = sign_in("")
    assert again["name"] == first["name"]
    rows = store.get("openai-codex").credentials
    assert len(rows) == 1 and rows[0].label == "Personal 工作" and rows[0].payload.auth_value == "access-again"


# ---- changes ------------------------------------------------------------

def test_a_held_sign_in_read_answers_when_the_sign_in_moves(client, monkeypatch):
    """A window reads the next step of a sign-in held at the worker, not on a timer."""
    import time
    from openprogram.models.auth.login import login_driver
    import openprogram.models.listing as listing
    c, _store = client
    monkeypatch.setattr(listing, "fetch_models_remote", lambda name: None)

    async def slow_login(provider, method, ui, *, api_key=None, label="", source="web"):
        await asyncio.sleep(0.3)  # the browser callback arriving
        await ui.show_progress("Completing sign-in…")
        return Credential(provider_id=provider, kind="oauth", label=label, source=source,
                          identity={"email": "me@example.com"},
                          payload=CredentialData(kind="oauth", auth_value="access"))

    monkeypatch.setattr(login_driver, "run_login", slow_login)
    sid = c.post("/api/providers/openai-codex/login/start", json={"method": "pkce_oauth"}).json()["session"]
    first = c.get("/api/providers/openai-codex/login/poll", params={"session": sid}).json()
    assert first["done"] is False and first["events"] == []
    started = time.monotonic()
    held = c.get("/api/providers/openai-codex/login/poll", params={
        "session": sid, "cursor": first["cursor"], "version": first["version"], "wait": 20}).json()
    assert time.monotonic() - started < 10, "answered when the sign-in moved, not at the end of the wait"
    assert held["events"] and held["version"] > first["version"]
    last = held
    while not last["done"]:
        last = c.get("/api/providers/openai-codex/login/poll", params={
            "session": sid, "cursor": last["cursor"], "version": last["version"], "wait": 20}).json()
    assert last["ok"] is True
    started = time.monotonic()
    again = c.get("/api/providers/openai-codex/login/poll", params={
        "session": sid, "cursor": last["cursor"], "version": last["version"], "wait": 20}).json()
    assert again["done"] is True and time.monotonic() - started < 5, "a finished sign-in is never held"


def test_rename_and_turn_off(client):
    c, store = client
    cid = _key(store, "a", "sk-aaaa-0000001")
    body = c.patch(f"/api/providers/openai/credentials/{cid}", json={"label": "Personal", "enabled": False}).json()
    assert body["ok"] is True
    assert (body["credential"]["label"], body["credential"]["enabled"]) == ("Personal", False)
    assert c.patch(f"/api/providers/openai/credentials/{cid}", json={"enabled": "no"}).status_code == 400
    assert c.patch(f"/api/providers/openai/credentials/{cid}", json={"secret": 1}).status_code == 400
    assert c.patch("/api/providers/openai/credentials/nope", json={"label": "x"}).status_code == 404


def test_remove_and_remove_missing(client):
    c, store = client
    cid = _key(store, "a", "sk-aaaa-0000001")
    assert c.delete(f"/api/providers/openai/credentials/{cid}").json() == {"ok": True}
    assert store.get("openai").credentials == []
    assert c.delete(f"/api/providers/openai/credentials/{cid}").status_code == 404


def test_reorder_needs_every_id_once(client):
    c, store = client
    a, b = _key(store, "a", "sk-aaaa-0000001"), _key(store, "b", "sk-bbbb-0000002")
    body = c.post("/api/providers/openai/credentials/reorder", json={"order": [b, a]}).json()
    assert [row["id"] for row in body["credentials"]] == [b, a]
    for bad in ([a, a], "ab", [a, 1]):
        assert c.post("/api/providers/openai/credentials/reorder", json={"order": bad}).status_code == 400
    # A well-formed order of a list that has changed since is stale, not malformed.
    for stale in ([a], [a, b, "x"]):
        assert c.post("/api/providers/openai/credentials/reorder", json={"order": stale}).status_code == 409
    assert [x.id for x in store.get("openai").credentials] == [b, a]


def test_strategy(client):
    c, _ = client
    assert c.post("/api/providers/openai/credentials/strategy",
                  json={"strategy": "evenly"}).json()["strategy"] == "evenly"
    assert c.post("/api/providers/openai/credentials/strategy",
                  json={"strategy": "round_robin"}).status_code == 400


def test_check_records_the_outcome(client, monkeypatch):
    from openprogram.models.listing import credentials
    c, store = client
    cid = _key(store, "a", "sk-aaaa-0000001")
    monkeypatch.setattr(credentials, "check_credential", lambda *a, **k: SimpleNamespace(
        status="invalid_credential", detail="rejected", to_dict=lambda: {"status": "invalid_credential"}))
    body = c.post(f"/api/providers/openai/credentials/{cid}/check").json()
    assert body["credential"]["status"] == "invalid_key"
    assert body["check"]


# ---- a conversation's pin -----------------------------------------------

def test_a_pin_is_kept_on_the_conversation(client, tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    c, store = client
    cid = _key(store, "a", "sk-aaaa-0000001")
    sessions = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.state.store.session.session_store.shared._default_store",
                        sessions, raising=False)
    sessions.create_session("s1", "main")
    assert c.post("/api/sessions/s1/credentials", json={"provider": "openai", "credential_id": cid}).json()["pin"] \
        == {"openai": cid}
    assert c.post("/api/sessions/none/credentials", json={"provider": "openai", "credential_id": None}).status_code == 404
    assert c.get("/api/sessions/s1/credentials").json()["pin"] == {"openai": cid}
    assert c.post("/api/sessions/s1/credentials", json={"provider": "openai", "credential_id": "nope"}).status_code == 404
    assert c.post("/api/sessions/s1/credentials", json={"provider": "openai", "credential_id": None}).json()["pin"] == {}
    assert c.post("/api/sessions/s1/credentials", json={"provider": "openai"}).status_code == 400


# ---- discover -----------------------------------------------------------

def test_discover_returns_list(client):
    c, _ = client
    r = c.post("/api/providers/discover")
    assert r.status_code == 200
    for entry in r.json()["discovered"]:
        assert "source_id" in entry


# ---- SSE plumbing ------------------------------------------------------
#
# End-to-end SSE (httpx streaming through TestClient) is tested in
# integration; these unit tests exercise the listener hook directly so
# we don't depend on the sync/async streaming bridge behaving.


def test_event_listener_fans_out_to_subscribers(client):
    """Direct test of the store listener: a registered subscriber queue
    receives events cross-thread via call_soon_threadsafe."""
    c, store = client

    async def scenario():
        auth_routes._wire_store_listener()
        q: asyncio.Queue[AuthEvent] = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()
        entry = (q, loop)
        auth_routes._subscribers.add(entry)
        try:
            # Emit from the same thread; call_soon_threadsafe is no-op-ish.
            store.emit(AuthEvent(
                type=AuthEventType.REFRESH_SUCCEEDED,
                provider_id="openai-codex",
                credential_id="cred_abc",
            ))
            event = await asyncio.wait_for(q.get(), timeout=30)
            assert event.type == AuthEventType.REFRESH_SUCCEEDED
            assert event.provider_id == "openai-codex"
            assert event.credential_id == "cred_abc"
        finally:
            auth_routes._subscribers.discard(entry)

    asyncio.run(scenario())


def test_event_to_json_has_expected_shape():
    event = AuthEvent(
        type=AuthEventType.POOL_EXHAUSTED,
        provider_id="prov",
        credential_id="cid",
        detail={"reason": "everyone is cooling down"},
    )
    encoded = auth_routes._event_to_json(event)
    payload = json.loads(encoded)
    assert payload["type"] == "pool_exhausted"
    assert payload["provider_id"] == "prov"
    assert payload["detail"]["reason"].startswith("everyone")
    assert "timestamp_ms" in payload


def test_put_nowait_with_drop_drops_oldest_on_overflow():
    q: asyncio.Queue[AuthEvent] = asyncio.Queue(maxsize=2)
    mk = lambda suffix: AuthEvent(
        type=AuthEventType.LOGIN_SUCCEEDED, credential_id=f"c_{suffix}",
    )
    auth_routes._put_nowait_with_drop(q, mk("a"))
    auth_routes._put_nowait_with_drop(q, mk("b"))
    auth_routes._put_nowait_with_drop(q, mk("c"))  # evicts "a"
    assert q.qsize() == 2
    first = q.get_nowait()
    second = q.get_nowait()
    assert first.credential_id == "c_b"
    assert second.credential_id == "c_c"


# ---- doctor / adopt_all / aliases ----------------------------------------

def test_doctor_route_empty_store(client):
    c, _ = client
    body = c.post("/api/providers/doctor").json()
    assert body["pools_checked"] == 0
    assert "no_pools" in {f["code"] for f in body["findings"]}


def test_doctor_route_flags_expired_oauth(client):
    c, store = client
    store.add_credential(Credential(
        provider_id="random-oauth-provider", kind="oauth", source="cli",
        payload=CredentialData(kind="oauth", auth_value="t-expired",
                               data={"refresh_token": "r-abc", "expires_at_ms": 1})))
    codes = {f["code"] for f in c.post("/api/providers/doctor").json()["findings"]}
    assert "expired_no_refresh" in codes


def test_adopt_all_route_answers_a_report(client, monkeypatch):
    from openprogram.models.auth import adoption
    c, _ = client
    monkeypatch.setattr(adoption, "_sources", lambda: [])
    body = c.post("/api/providers/adopt_all").json()
    assert body["adopted"] == 0


def test_aliases_route_returns_table(client):
    c, _ = client
    body = c.get("/api/providers/aliases").json()
    assert body.get("codex") == "openai-codex"
    assert body.get("claude") == "anthropic"
