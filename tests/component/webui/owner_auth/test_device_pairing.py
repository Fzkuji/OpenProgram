"""Another device pairs once and stays signed in across Web restarts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openprogram.interfaces.server.device_access import (
    IDLE_LIMIT_SECONDS,
    PAIRING_TTL_SECONDS,
    DeviceRegistry,
    label_from_user_agent,
)
from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState


RAW_TOKEN = bytes(range(32))
OWNER_PRINCIPAL_ID = "owner/install/0123456789abcdef"
LAN_ORIGIN = "http://192.168.1.20:18100"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _state(devices: DeviceRegistry | None = None) -> OwnerAuthState:
    state = OwnerAuthState.from_raw_token(
        RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
        bind_host="0.0.0.0",
        port=18100,
        allowed_origins=(LAN_ORIGIN,),
    )
    if devices is not None:
        state.devices = devices
    return state


def _app(state: OwnerAuthState):
    async def api(request: Request):
        return JSONResponse({
            "tier": request.scope["state"]["authority"].get("authority_tier"),
            "device": request.scope["state"].get("device_id"),
        })

    async def socket(ws):
        await ws.accept()
        await ws.send_text("hello")
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            return

    app = Starlette(routes=[
        Route("/api/x", api, methods=["GET", "POST"]),
        WebSocketRoute("/ws", socket),
    ])
    return OwnerAuthMiddleware(app, auth_state=state)


def _owner(state):
    return {"Authorization": f"Bearer {state.token}"}


def test_browser_pairs_with_a_code_and_keeps_a_device_cookie():
    state = _state()
    code, _expires = state.devices.create_pairing()
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1"
    with TestClient(_app(state), base_url=LAN_ORIGIN) as client:
        response = client.post(
            "/api/auth/pair",
            headers={"origin": LAN_ORIGIN, "user-agent": iphone},
            json={"code": code},
        )
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{state.device_cookie_name}=opd1.")
        assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
        assert "Max-Age=34560000" in cookie and "Domain=" not in cookie
        assert code not in cookie

        body = client.get("/api/x").json()
        assert body["tier"] == "owner"
        assert body["device"] is not None
        assert client.get("/api/auth/session").status_code == 204
        # Cookie mutations still need the exact Origin.
        assert client.post("/api/x").status_code == 403

    [device] = state.devices.list()
    assert device["label"] == "Safari on iPhone"


def test_a_code_works_once_and_never_after_it_expires():
    clock = Clock()
    state = _state(DeviceRegistry(clock=clock))
    code, _ = state.devices.create_pairing()
    stale, _ = state.devices.create_pairing()
    with TestClient(_app(state), base_url=LAN_ORIGIN) as client:
        assert client.post("/api/auth/pair", json={"code": code}).status_code == 200
        again = client.post("/api/auth/pair", json={"code": code})
        assert again.status_code == 401
        assert again.json() == {"error": "pairing_rejected"}
        clock.now += PAIRING_TTL_SECONDS + 1
        assert client.post("/api/auth/pair", json={"code": stale}).status_code == 401


@pytest.mark.parametrize("body", [
    b"{}",
    b'{"code":"short"}',
    b'{"code":"x","extra":1}',
    b"x" * 513,
])
def test_bad_pair_requests_are_refused_alike(body):
    state = _state()
    state.devices.create_pairing()
    with TestClient(_app(state), base_url=LAN_ORIGIN) as client:
        response = client.post(
            "/api/auth/pair",
            headers={"content-type": "application/json"},
            content=body,
        )
        assert response.status_code == 401
        assert response.json() == {"error": "pairing_rejected"}


def test_native_client_gets_a_bearer_that_survives_a_new_owner_token(tmp_path: Path):
    path = tmp_path / "web" / "devices.json"
    first = _state(DeviceRegistry(path))
    code, _ = first.devices.create_pairing(label="Work laptop")
    with TestClient(_app(first), base_url=LAN_ORIGIN) as client:
        paired = client.post("/api/auth/pair", json={"code": code}).json()
    credential = paired["token"]
    assert credential.startswith("opd1.")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    stored = path.read_text(encoding="utf-8")
    assert credential.rsplit(".", 1)[1] not in stored

    # A restart: new owner token, registry read back from disk.
    restarted = OwnerAuthState.from_raw_token(
        bytes(reversed(range(32))),
        owner_principal_id=OWNER_PRINCIPAL_ID,
        bind_host="0.0.0.0",
        port=18100,
        allowed_origins=(LAN_ORIGIN,),
    )
    restarted.devices = DeviceRegistry(path)
    with TestClient(_app(restarted), base_url=LAN_ORIGIN) as client:
        body = client.get("/api/x", headers={"Authorization": f"Bearer {credential}"}).json()
        assert body == {"tier": "owner", "device": paired["device_id"]}
        # A wrong bearer never falls back to anything.
        assert client.get(
            "/api/x", headers={"Authorization": f"Bearer {credential[:-1]}A"}
        ).status_code == 401
    assert restarted.devices.list()[0]["label"] == "Work laptop"


def test_idle_devices_are_forgotten():
    clock = Clock()
    registry = DeviceRegistry(clock=clock)
    code, _ = registry.create_pairing()
    _device, credential = registry.redeem(code)
    clock.now += IDLE_LIMIT_SECONDS - 10
    assert registry.verify(credential) is not None
    clock.now += IDLE_LIMIT_SECONDS + 1
    assert registry.verify(credential) is None
    assert registry.list() == []


def test_revoking_a_device_refuses_it_and_closes_its_socket():
    state = _state()
    code, _ = state.devices.create_pairing()
    with TestClient(_app(state), base_url=LAN_ORIGIN) as client:
        paired = client.post("/api/auth/pair", json={"code": code}).json()
        bearer = {"Authorization": f"Bearer {paired['token']}"}
        with client.websocket_connect("/ws", headers={**bearer, "host": "192.168.1.20:18100"}) as ws:
            assert ws.receive_text() == "hello"
            assert state.devices.revoke(paired["device_id"])
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code == 4401
        assert client.get("/api/x", headers=bearer).status_code == 401
        # The owner's own clients are untouched.
        assert client.get("/api/x", headers=_owner(state)).status_code == 200


def test_device_routes_list_rename_revoke_and_mint_codes():
    from openprogram.interfaces.server.routes.identity.devices import router

    state = _state()
    app = FastAPI()
    app.state.owner_auth = state
    app.include_router(router)
    wrapped = OwnerAuthMiddleware(app, auth_state=state)
    with TestClient(wrapped, base_url=LAN_ORIGIN) as client:
        minted = client.post("/api/auth/pairings", headers=_owner(state), json={"label": "Phone"})
        assert minted.status_code == 200
        assert minted.json()["origins"] == [LAN_ORIGIN]
        paired = client.post("/api/auth/pair", json={"code": minted.json()["code"]}).json()

        listing = client.get("/api/auth/devices", headers={"Authorization": f"Bearer {paired['token']}"}).json()
        assert listing["current"] == paired["device_id"]
        assert [d["label"] for d in listing["devices"]] == ["Phone"]
        assert "secret_hash" not in json.dumps(listing)

        assert client.patch(
            f"/api/auth/devices/{paired['device_id']}",
            headers=_owner(state), json={"label": "My phone"},
        ).status_code == 204
        assert state.devices.list()[0]["label"] == "My phone"
        assert client.delete(f"/api/auth/devices/{paired['device_id']}", headers=_owner(state)).status_code == 204
        assert client.delete(f"/api/auth/devices/{paired['device_id']}", headers=_owner(state)).status_code == 404


@pytest.mark.parametrize("ua,label", [
    ("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36", "Chrome on Android"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", "Safari on Mac"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36 Edg/131.0", "Edge on Windows"),
    ("", "Device"),
])
def test_label_from_user_agent(ua, label):
    assert label_from_user_agent(ua) == label
