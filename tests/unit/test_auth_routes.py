"""Tests for the auth REST + SSE routes (openprogram/webui/_auth_routes.py).

Uses a minimal FastAPI app with just the auth router — we don't pull in
server.py's full surface (which drags runtime / persistence / websocket
infra). The router is the unit; the rest is integration."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.auth.manager import AuthManager, set_manager_for_testing
from openprogram.auth.profiles import (
    DEFAULT_PROFILE_NAME,
    ProfileManager,
    set_profile_manager_for_testing,
)
from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import (
    ApiKeyPayload,
    AuthEvent,
    AuthEventType,
    Credential,
    CredentialPool,
    OAuthPayload,
)
from openprogram.webui import _auth_routes
from openprogram.webui._auth_routes import router


@pytest.fixture
def client(tmp_path):
    """FastAPI test client with isolated store + profile manager."""
    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    set_manager_for_testing(AuthManager(store=store))

    pm = ProfileManager(root=tmp_path / "profiles")
    set_profile_manager_for_testing(pm)

    # Reset the SSE wire-up so each test gets a fresh listener on our new store.
    _auth_routes._wired = False
    _auth_routes._subscribers.clear()

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c, store, pm

    set_store_for_testing(None)
    set_manager_for_testing(None)
    set_profile_manager_for_testing(None)


# ---- profiles ---------------------------------------------------------

def test_list_profiles_includes_default(client):
    c, store, pm = client
    resp = c.get("/api/providers/profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default"] == DEFAULT_PROFILE_NAME
    names = [p["name"] for p in body["profiles"]]
    assert DEFAULT_PROFILE_NAME in names


def test_create_profile_ok(client):
    c, *_ = client
    resp = c.post("/api/providers/profiles", json={"name": "work", "display_name": "Work"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "work"
    assert resp.json()["display_name"] == "Work"


def test_create_profile_duplicate_is_400(client):
    c, *_ = client
    c.post("/api/providers/profiles", json={"name": "dup"})
    r = c.post("/api/providers/profiles", json={"name": "dup"})
    assert r.status_code == 400


def test_create_profile_invalid_name_is_400(client):
    c, *_ = client
    r = c.post("/api/providers/profiles", json={"name": "../evil"})
    assert r.status_code == 400


def test_delete_profile(client):
    c, *_ = client
    c.post("/api/providers/profiles", json={"name": "scratch"})
    r = c.delete("/api/providers/profiles/scratch")
    assert r.status_code == 200
    r = c.get("/api/providers/profiles")
    assert "scratch" not in [p["name"] for p in r.json()["profiles"]]


def test_delete_default_profile_forbidden(client):
    c, *_ = client
    r = c.delete(f"/api/providers/profiles/{DEFAULT_PROFILE_NAME}")
    assert r.status_code == 400


# ---- pools + credentials ----------------------------------------------

def test_list_pools_empty(client):
    c, *_ = client
    r = c.get("/api/providers/pools")
    assert r.status_code == 200
    assert r.json() == {"pools": []}


def test_add_api_key_and_list(client):
    c, store, _ = client
    r = c.post(
        "/api/providers/pools/openai/default/credentials",
        json={"type": "api_key", "api_key": "sk-abcdef-longenough-1234"},
    )
    assert r.status_code == 200
    view = r.json()
    assert view["kind"] == "api_key"
    # Secret must be masked.
    assert view["payload"]["api_key_preview"] != "sk-abcdef-longenough-1234"
    assert "…" in view["payload"]["api_key_preview"]

    r = c.get("/api/providers/pools")
    body = r.json()
    assert len(body["pools"]) == 1
    assert body["pools"][0]["credentials"][0]["kind"] == "api_key"


def test_add_oauth_credential(client):
    c, *_ = client
    r = c.post(
        "/api/providers/pools/anthropic/default/credentials",
        json={
            "type": "oauth",
            "access_token": "ACC-tokenvalue-here",
            "refresh_token": "REF-token",
            "expires_at_ms": 1712345678901,
            "client_id": "client123",
        },
    )
    assert r.status_code == 200
    view = r.json()
    assert view["kind"] == "oauth"
    assert view["payload"]["type"] == "oauth"
    assert view["payload"]["has_refresh_token"] is True
    assert view["payload"]["client_id"] == "client123"


def test_add_missing_required_field_is_400(client):
    c, *_ = client
    r = c.post(
        "/api/providers/pools/openai/default/credentials",
        json={"type": "api_key"},
    )
    assert r.status_code == 400


def test_add_unknown_type_is_400(client):
    c, *_ = client
    r = c.post(
        "/api/providers/pools/openai/default/credentials",
        json={"type": "magic_key", "api_key": "x"},
    )
    assert r.status_code == 400


def test_get_pool_not_found(client):
    c, *_ = client
    r = c.get("/api/providers/pools/nothing/default")
    assert r.status_code == 404


def test_remove_credential(client):
    c, store, _ = client
    c.post(
        "/api/providers/pools/openai/default/credentials",
        json={"type": "api_key", "api_key": "sk-xxxxxxxxxxxxxxxx"},
    )
    pool = store.find_pool("openai", "default")
    cred_id = pool.credentials[0].credential_id
    r = c.delete(f"/api/providers/pools/openai/default/credentials/{cred_id}")
    assert r.status_code == 200
    assert r.json()["removed"] == cred_id
    # Cred is gone.
    pool = store.find_pool("openai", "default")
    assert pool is None or not pool.credentials


def test_remove_nonexistent_credential_is_404(client):
    c, *_ = client
    r = c.delete("/api/providers/pools/openai/default/credentials/cred_nope")
    assert r.status_code == 404


def test_list_pools_filtered_by_profile(client):
    c, store, pm = client
    pm.create_profile("work")
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="work",
        credentials=[Credential(
            provider_id="openai", profile_id="work", kind="api_key",
            payload=ApiKeyPayload(api_key="work-key"),
        )],
    ))
    store.put_pool(CredentialPool(
        provider_id="openai", profile_id="default",
        credentials=[Credential(
            provider_id="openai", profile_id="default", kind="api_key",
            payload=ApiKeyPayload(api_key="default-key"),
        )],
    ))
    r = c.get("/api/providers/pools?profile=work")
    body = r.json()
    assert len(body["pools"]) == 1
    assert body["pools"][0]["profile_id"] == "work"


# ---- discover ---------------------------------------------------------

def test_discover_returns_list(client, monkeypatch):
    c, *_ = client
    # Clear all provider env vars so EnvApiKeySources return empty.
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    for var in list(PROVIDER_ENV_VARS.values()) + [
        "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
        "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)
    r = c.post("/api/providers/discover")
    assert r.status_code == 200
    assert "discovered" in r.json()
    # On a clean machine nothing is found; on a dev machine many things
    # might be. We only assert the shape, not the count.
    for entry in r.json()["discovered"]:
        assert "source_id" in entry


def test_discover_picks_up_env_var(client, monkeypatch):
    c, *_ = client
    monkeypatch.setenv("OPENAI_API_KEY", "sk-discoverable")
    r = c.post("/api/providers/discover")
    found = r.json()["discovered"]
    env_found = [e for e in found if e.get("source_id") == "env:OPENAI_API_KEY"]
    assert env_found, f"expected env:OPENAI_API_KEY in {[e.get('source_id') for e in found]}"
    # Credential view must mask the secret.
    cred_view = env_found[0]["credential"]
    assert cred_view["payload"]["api_key_preview"] != "sk-discoverable"


# ---- SSE plumbing ------------------------------------------------------
#
# End-to-end SSE (httpx streaming through TestClient) is tested in
# integration; these unit tests exercise the listener hook directly so
# we don't depend on the sync/async streaming bridge behaving.


def test_event_listener_fans_out_to_subscribers(client):
    """Direct test of the store listener: a registered subscriber queue
    receives events cross-thread via call_soon_threadsafe."""
    c, store, _ = client

    async def scenario():
        _auth_routes._wire_store_listener()
        q: asyncio.Queue[AuthEvent] = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()
        entry = (q, loop)
        _auth_routes._subscribers.add(entry)
        try:
            # Emit from the same thread; call_soon_threadsafe is no-op-ish.
            store._emit(AuthEvent(
                type=AuthEventType.REFRESH_SUCCEEDED,
                provider_id="chatgpt-subscription",
                profile_id="default",
                credential_id="cred_abc",
            ))
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            assert event.type == AuthEventType.REFRESH_SUCCEEDED
            assert event.provider_id == "chatgpt-subscription"
            assert event.credential_id == "cred_abc"
        finally:
            _auth_routes._subscribers.discard(entry)

    asyncio.run(scenario())


def test_event_to_json_has_expected_shape():
    event = AuthEvent(
        type=AuthEventType.POOL_EXHAUSTED,
        provider_id="prov",
        profile_id="prof",
        credential_id="cid",
        detail={"reason": "everyone is cooling down"},
    )
    encoded = _auth_routes._event_to_json(event)
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
    _auth_routes._put_nowait_with_drop(q, mk("a"))
    _auth_routes._put_nowait_with_drop(q, mk("b"))
    _auth_routes._put_nowait_with_drop(q, mk("c"))  # evicts "a"
    assert q.qsize() == 2
    first = q.get_nowait()
    second = q.get_nowait()
    assert first.credential_id == "c_b"
    assert second.credential_id == "c_c"


# ---- doctor / adopt_all / aliases ----------------------------------------

def test_doctor_route_empty_store(client):
    c, _, _ = client
    resp = c.post("/api/providers/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pools_checked"] == 0
    codes = {f["code"] for f in body["findings"]}
    assert "no_pools" in codes


def test_doctor_route_flags_expired_oauth(client):
    c, store, _ = client
    store.put_pool(CredentialPool(
        provider_id="random-oauth-provider", profile_id="default",
        credentials=[Credential(
            provider_id="random-oauth-provider", profile_id="default",
            kind="oauth",
            payload=OAuthPayload(
                access_token="t-expired", refresh_token="r-abc",
                expires_at_ms=1,
            ),
            source="cli_paste",
        )],
    ))
    body = c.post("/api/providers/doctor").json()
    codes = {f["code"] for f in body["findings"]}
    assert "expired_no_refresh" in codes


def test_adopt_all_route_picks_up_env_var(client, monkeypatch):
    c, store, _ = client
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    # Wipe every var discover() touches so the dev machine's real keys
    # don't pollute the test.
    for v in set(PROVIDER_ENV_VARS.values()):
        monkeypatch.delenv(v, raising=False)
    for v in ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN",
              "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"]:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-route-adopt-all-9999")

    body = c.post("/api/providers/adopt_all").json()
    assert body["adopted"] >= 1
    assert store.find_pool("openai", "default") is not None


def test_aliases_route_returns_table(client):
    c, _, _ = client
    resp = c.get("/api/providers/aliases")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("codex") == "chatgpt-subscription"
    assert body.get("claude") == "anthropic"
