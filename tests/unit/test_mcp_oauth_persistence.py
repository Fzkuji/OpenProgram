"""MCP OAuth persistence — tokens survive restarts, refresh is silent.

Covers the regression where every worker restart re-opened the browser
consent page: the persisted access token carried only the relative
``expires_in``, so a fresh process treated a stale token as live, sent
it, got a 401, and the SDK's 401 branch ran the full browser flow
without ever trying the refresh token.

All HTTP is mocked via ``httpx.MockTransport`` — no network.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse

import httpx
import pytest
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from openprogram.mcp.token_storage import (
    FileTokenStorage,
    PersistentOAuthProvider,
)


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    return tmp_path


def _provider(storage: FileTokenStorage) -> PersistentOAuthProvider:
    """Provider whose browser handlers fail the test if invoked."""

    async def _fail_redirect(url: str) -> None:
        raise AssertionError(f"browser flow triggered: {url}")

    async def _fail_callback():
        raise AssertionError("callback awaited — browser flow triggered")

    return PersistentOAuthProvider(
        server_url="https://api.example.com/mcp",
        client_metadata=OAuthClientMetadata(
            redirect_uris=["http://127.0.0.1:43125/callback"],
        ),
        storage=storage,
        redirect_handler=_fail_redirect,
        callback_handler=_fail_callback,
    )


def _seed_authenticated_server(name: str = "srv",
                               *, expired: bool = True) -> FileTokenStorage:
    """Storage state as left behind by a completed first-time OAuth
    flow: tokens + dynamic client registration + discovery."""
    storage = FileTokenStorage(name)
    asyncio.run(storage.set_tokens(OAuthToken(
        access_token="stale", refresh_token="rt", expires_in=3600,
    )))
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(
        client_id="cid",
        redirect_uris=["http://127.0.0.1:43125/callback"],
    )))
    storage.set_discovery({
        "oauth_metadata": {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/oauth/token",
        },
        "protected_resource_metadata": None,
        "auth_server_url": "https://auth.example.com",
    })
    if expired:
        # Simulate the passage of time since issuance.
        path = storage.path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["expires_at"] = time.time() - 10
        path.write_text(json.dumps(data), encoding="utf-8")
    return storage


# -- FileTokenStorage extras -----------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_clear_rejects_symlink_without_deleting_target(tmp_path, monkeypatch):
    from openprogram.credential_files import PrivateAtomicWriteError

    tokens = tmp_path / "mcp_tokens"
    tokens.mkdir()
    monkeypatch.setattr(
        "openprogram.mcp.token_storage.get_tokens_dir", lambda: tokens
    )
    outside = tmp_path / "outside.json"
    outside.write_text("outside")
    storage = FileTokenStorage("srv")
    storage.path().symlink_to(outside)

    with pytest.raises(PrivateAtomicWriteError):
        storage.clear()

    assert outside.read_text() == "outside"


def test_clear_returns_no_success_when_unlink_fails(tmp_path, monkeypatch):
    from openprogram import credential_files
    from openprogram.credential_files import PrivateAtomicWriteError

    tokens = tmp_path / "mcp_tokens"
    tokens.mkdir()
    monkeypatch.setattr(
        "openprogram.mcp.token_storage.get_tokens_dir", lambda: tokens
    )
    storage = FileTokenStorage("srv")
    storage.path().write_text("{}")
    storage.path().chmod(0o600)
    monkeypatch.setattr(
        credential_files.os,
        "unlink",
        lambda _path: (_ for _ in ()).throw(OSError("unlink denied")),
    )

    with pytest.raises(PrivateAtomicWriteError) as caught:
        storage.clear()

    assert caught.value.code == "delete"
    assert caught.value.committed is False
    assert storage.path().exists()

def test_set_tokens_records_absolute_expiry(state_dir):
    storage = FileTokenStorage("srv")
    asyncio.run(storage.set_tokens(OAuthToken(
        access_token="at", refresh_token="rt", expires_in=3600,
    )))
    exp = storage.expires_at()
    assert exp is not None
    assert abs(exp - (time.time() + 3600)) < 5
    loaded = asyncio.run(storage.get_tokens())
    assert loaded is not None
    assert loaded.access_token == "at"
    assert loaded.refresh_token == "rt"


def test_expiry_cleared_when_server_omits_expires_in(state_dir):
    storage = FileTokenStorage("srv")
    asyncio.run(storage.set_tokens(OAuthToken(
        access_token="a", expires_in=60,
    )))
    asyncio.run(storage.set_tokens(OAuthToken(access_token="b")))
    assert storage.expires_at() is None


def test_stored_redirect_port_reads_registered_uri(state_dir):
    storage = FileTokenStorage("srv")
    assert storage.stored_redirect_port() is None
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(
        client_id="cid",
        redirect_uris=["http://127.0.0.1:43125/callback"],
    )))
    assert storage.stored_redirect_port() == 43125


def test_discovery_round_trip(state_dir):
    storage = FileTokenStorage("srv")
    assert storage.get_discovery() is None
    storage.set_discovery({"auth_server_url": "https://auth.example.com"})
    got = storage.get_discovery()
    assert got == {"auth_server_url": "https://auth.example.com"}


# -- PersistentOAuthProvider -----------------------------------------

def test_initialize_restores_expiry_and_discovery(state_dir):
    storage = _seed_authenticated_server()
    provider = _provider(storage)
    asyncio.run(provider._initialize())

    ctx = provider.context
    # Stale token is recognised as stale (stock SDK would treat it as
    # valid because token_expiry_time stays None after a restart).
    assert ctx.token_expiry_time is not None
    assert ctx.token_expiry_time < time.time()
    assert not ctx.is_token_valid()
    # Silent-renewal path is open.
    assert ctx.can_refresh_token()
    # Discovery restored: refresh will hit the real token endpoint,
    # not the <server>/token fallback.
    assert ctx.oauth_metadata is not None
    assert str(ctx.oauth_metadata.token_endpoint) == (
        "https://auth.example.com/oauth/token"
    )
    assert ctx.auth_server_url == "https://auth.example.com"


def test_expired_token_refreshes_silently_without_browser(state_dir):
    """The regression test: restart + expired access token must renew
    via refresh_token in the background — no redirect, no browser."""
    storage = _seed_authenticated_server()
    provider = _provider(storage)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if str(request.url) == "https://auth.example.com/oauth/token":
            form = urllib.parse.parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == ["rt"]
            assert form["client_id"] == ["cid"]
            return httpx.Response(200, json={
                "access_token": "fresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "rt2",
            })
        assert request.headers.get("Authorization") == "Bearer fresh"
        return httpx.Response(200, json={"ok": True})

    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), auth=provider,
        ) as cx:
            resp = await cx.get("https://api.example.com/mcp")
            assert resp.status_code == 200

    asyncio.run(_run())
    assert seen == [
        "POST https://auth.example.com/oauth/token",
        "GET https://api.example.com/mcp",
    ]
    # Rotated tokens + fresh absolute expiry persisted for next restart.
    loaded = asyncio.run(storage.get_tokens())
    assert loaded is not None
    assert loaded.access_token == "fresh"
    assert loaded.refresh_token == "rt2"
    assert storage.expires_at() is not None
    assert storage.expires_at() > time.time()


def test_refresh_without_rotation_keeps_old_refresh_token(state_dir):
    """RFC 6749 §6: a refresh response MAY omit refresh_token, meaning
    the old one stays valid. The stock SDK drops it (forcing a browser
    flow at next expiry); we must keep it, in memory and on disk."""
    storage = _seed_authenticated_server()
    provider = _provider(storage)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://auth.example.com/oauth/token":
            return httpx.Response(200, json={
                "access_token": "fresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                # no refresh_token — server chose not to rotate
            })
        return httpx.Response(200, json={"ok": True})

    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), auth=provider,
        ) as cx:
            resp = await cx.get("https://api.example.com/mcp")
            assert resp.status_code == 200

    asyncio.run(_run())
    assert provider.context.current_tokens is not None
    assert provider.context.current_tokens.refresh_token == "rt"
    loaded = asyncio.run(storage.get_tokens())
    assert loaded is not None
    assert loaded.refresh_token == "rt"


def test_valid_token_used_directly(state_dir):
    """A still-live persisted token is sent as-is — no refresh, no
    browser."""
    storage = _seed_authenticated_server(expired=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer stale"
        return httpx.Response(200, json={"ok": True})

    provider = _provider(storage)

    async def _run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), auth=provider,
        ) as cx:
            resp = await cx.get("https://api.example.com/mcp")
            assert resp.status_code == 200

    asyncio.run(_run())


# -- MCPClient._build_remote_auth ------------------------------------

def test_build_remote_auth_reuses_registered_port(state_dir):
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import (
        AUTH_OAUTH,
        HTTP,
        MCPServerConfig,
        OAuthSettings,
    )

    storage = FileTokenStorage("srv")
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(
        client_id="cid",
        redirect_uris=["http://127.0.0.1:43125/callback"],
    )))
    cfg = MCPServerConfig(
        name="srv", type=HTTP, url="https://api.example.com/mcp",
        auth_kind=AUTH_OAUTH, oauth=OAuthSettings(),
    )
    _headers, auth = asyncio.run(MCPClient(cfg)._build_remote_auth())
    assert isinstance(auth, PersistentOAuthProvider)
    # Re-auth presents the same redirect_uri the client was registered
    # with instead of a fresh random port.
    assert str(auth.context.client_metadata.redirect_uris[0]) == (
        "http://127.0.0.1:43125/callback"
    )


def test_build_remote_auth_seeds_preregistered_client(state_dir):
    from openprogram.mcp.client import MCPClient
    from openprogram.mcp.config import (
        AUTH_OAUTH,
        HTTP,
        MCPServerConfig,
        OAuthSettings,
    )

    cfg = MCPServerConfig(
        name="srv2", type=HTTP, url="https://api.example.com/mcp",
        auth_kind=AUTH_OAUTH,
        oauth=OAuthSettings(client_id="pre", redirect_port=51515),
    )
    asyncio.run(MCPClient(cfg)._build_remote_auth())
    # Seeded synchronously — the provider can never race an unfinished
    # seeding task into a redundant dynamic registration.
    info = asyncio.run(FileTokenStorage("srv2").get_client_info())
    assert info is not None
    assert info.client_id == "pre"
    assert str(info.redirect_uris[0]) == "http://127.0.0.1:51515/callback"
