"""The web surface refuses requests a browser makes on another site's behalf.

Two attacks reach an unauthenticated localhost server through the user's own
browser: cross-site WebSocket hijacking (the same-origin policy does not
cover WebSockets) and DNS rebinding (the attacker's own name resolves to
127.0.0.1, so its page *is* same-origin). The guard closes both before
routing, and must still let the terminal UI — which sends no Origin — in.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openprogram.webui.origin_guard import (
    BrowserOriginGuard,
    deny_reason,
    hostname_of,
    is_loopback_hostname,
)

LOCAL = "127.0.0.1:18100"


def _deny(**kw):
    kw.setdefault("host", LOCAL)
    kw.setdefault("origin", "")
    kw.setdefault("sec_fetch_site", "")
    return deny_reason(**kw)


# --- host parsing ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("127.0.0.1:18100", "127.0.0.1"),
    ("localhost:18100", "localhost"),
    ("[::1]:18100", "::1"),
    ("::1", "::1"),               # portless IPv6 literal survives intact
    ("evil.example", "evil.example"),
    ("", ""),
])
def test_hostname_of(raw, expected):
    assert hostname_of(raw) == expected


@pytest.mark.parametrize("name,ok", [
    ("127.0.0.1", True), ("127.0.0.53", True), ("localhost", True),
    ("::1", True), ("192.168.1.7", False), ("evil.example", False),
])
def test_is_loopback_hostname(name, ok):
    assert is_loopback_hostname(name) is ok


# --- what gets through ----------------------------------------------------

def test_no_origin_is_a_native_client():
    """The terminal UI uses npm `ws`, which sends no Origin."""
    assert _deny() is None


def test_same_origin_page_passes():
    assert _deny(origin="http://127.0.0.1:18100",
                 sec_fetch_site="same-origin") is None


def test_other_loopback_spelling_passes():
    """The UI on localhost talking to a 127.0.0.1 Host is still local."""
    assert _deny(origin="http://localhost:18100") is None


def test_configured_origin_passes():
    assert deny_reason(host="agent.internal", origin="https://agent.internal",
                       sec_fetch_site="cross-site",
                       allowed_origins=["https://agent.internal"],
                       enforce_loopback_host=False) is None


# --- what gets refused ----------------------------------------------------

def test_cross_site_origin_is_refused():
    assert _deny(origin="https://evil.example") == "origin_not_allowed"


def test_fetch_metadata_alone_is_enough():
    """The browser sets Sec-Fetch-Site; page JavaScript cannot forge it."""
    assert _deny(origin="http://127.0.0.1:18100",
                 sec_fetch_site="cross-site") == "cross_site_request"


def test_opaque_origin_is_refused():
    """A sandboxed iframe on any site sends Origin: null."""
    assert _deny(origin="null") == "origin_opaque"


def test_rebound_host_is_refused():
    """DNS rebinding: evil.example now resolves to 127.0.0.1, so its page
    is same-origin with the local server. The Host header still gives it
    away."""
    assert _deny(host="evil.example", origin="http://evil.example",
                 sec_fetch_site="same-origin") == "host_not_loopback"


def test_host_check_is_off_when_bound_beyond_loopback():
    assert deny_reason(host="10.0.0.5:18100", origin="http://10.0.0.5:18100",
                       sec_fetch_site="", enforce_loopback_host=False) is None


# --- the middleware, over a real connection -------------------------------

def _app():
    async def hello(_request):
        return PlainTextResponse("ok")

    async def socket(ws):
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()

    inner = Starlette(routes=[Route("/api/x", hello),
                              WebSocketRoute("/ws", socket)])
    return BrowserOriginGuard(inner)


def test_http_cross_site_gets_403():
    with TestClient(_app(), base_url=f"http://{LOCAL}") as client:
        r = client.get("/api/x", headers={"origin": "https://evil.example"})
        assert r.status_code == 403
        assert r.json() == {"error": "origin_not_allowed"}


def test_http_same_origin_is_served():
    with TestClient(_app(), base_url=f"http://{LOCAL}") as client:
        assert client.get("/api/x").text == "ok"


def test_websocket_cross_site_handshake_is_closed():
    """The attack the same-origin policy does not cover."""
    with TestClient(_app(), base_url=f"http://{LOCAL}") as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                "/ws", headers={"host": LOCAL,
                                "origin": "https://evil.example"},
            ) as ws:
                ws.receive_text()
        assert caught.value.code == 1008


def test_websocket_without_origin_connects():
    """The terminal UI's connection, which must keep working."""
    with TestClient(_app(), base_url=f"http://{LOCAL}") as client:
        with client.websocket_connect("/ws", headers={"host": LOCAL}) as ws:
            assert ws.receive_text() == "ok"
