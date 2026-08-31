from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openprogram.backend_endpoint import (
    OwnerAuthError,
    canonicalize_bind_host,
    canonicalize_origin,
    create_owner_challenge_proof,
    read_active_web_access,
)
from openprogram.webui.owner_auth import (
    OwnerAuthMiddleware,
    OwnerAuthState,
)
from openprogram.webui.routes import docs as docs_route


RAW_TOKEN = bytes(range(32))
OWNER_SUFFIX = "0123456789abcdef"
OWNER_PRINCIPAL_ID = f"owner/install/{OWNER_SUFFIX}"
LOCAL_ORIGIN = "http://127.0.0.1:18100"


def _state(
    *,
    host: str = "127.0.0.1",
    origins: tuple[str, ...] = (),
) -> OwnerAuthState:
    return OwnerAuthState.from_raw_token(
        RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
        bind_host=host,
        port=18100,
        allowed_origins=origins,
    )


def _app(state: OwnerAuthState) -> Starlette:
    async def shell(_request: Request):
        return HTMLResponse("<script>window.openprogramReady=true</script>")

    async def future_route(_request: Request):
        return PlainTextResponse("protected")

    async def health(_request: Request):
        return JSONResponse({"status": "ok"})

    async def api(request: Request):
        authority = request.scope.get("state", {}).get("authority", {})
        return JSONResponse({"tier": authority.get("authority_tier")})

    async def event_stream(_request: Request):
        async def events():
            yield b"data: ready\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async def socket(ws):
        await ws.accept()
        await ws.send_text(ws.scope["state"]["authority"]["authority_tier"])
        await ws.close()

    app = Starlette(routes=[
        Route("/", shell),
        Route("/future-route", future_route),
        Route("/healthz", health),
        Route("/api/x", api, methods=["GET", "POST"]),
        Route("/api/events", event_stream),
        WebSocketRoute("/ws", socket),
    ])
    return OwnerAuthMiddleware(app, auth_state=state)


def _bearer(state: OwnerAuthState) -> dict[str, str]:
    return {"Authorization": f"Bearer {state.token}"}


def test_process_token_is_owner_only_locked_and_replaced_after_release(
    tmp_path: Path,
):
    first = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    token_path = tmp_path / "web" / "token"
    try:
        assert token_path.read_text(encoding="ascii") == first.token
        assert len(first.token) == 43
        if os.name != "nt":
            assert token_path.stat().st_mode & 0o777 == 0o600
        assert first.fingerprint.startswith("sha256:")
        assert len(first.fingerprint) == len("sha256:") + 12
        assert first.cookie_name == "openprogram_owner_0123456789abcdef"
        assert len(first.cookie_value) == 43
        assert first.token not in repr(first)
        active_access = read_active_web_access(tmp_path)
        assert active_access.port == 18100
        assert active_access.effective_origins == first.effective_origins
        if os.name != "nt":
            assert (tmp_path / "web" / "access.json").stat().st_mode & 0o777 == 0o600

        with pytest.raises(OwnerAuthError, match="already active"):
            OwnerAuthState.start(
                state_dir=tmp_path,
                bind_host="127.0.0.1",
                port=18100,
                allowed_origins=(),
                raw_token=b"z" * 32,
                owner_principal_id=OWNER_PRINCIPAL_ID,
            )
        assert token_path.read_text(encoding="ascii") == first.token
    finally:
        first.close()

    assert not token_path.exists()
    assert not (tmp_path / "web" / "access.json").exists()
    second = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
        raw_token=b"z" * 32,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    try:
        assert second.token != first.token
    finally:
        second.close()


def test_access_snapshot_failure_removes_owned_token_and_releases_lock(
    monkeypatch,
    tmp_path: Path,
):
    from openprogram.webui import owner_auth

    write_private_text = owner_auth._write_private_text
    writes = 0

    def fail_second_write(path: Path, content: str) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("snapshot write failed")
        write_private_text(path, content)

    monkeypatch.setattr(owner_auth, "_write_private_text", fail_second_write)
    with pytest.raises(OSError, match="snapshot write failed"):
        OwnerAuthState.start(
            state_dir=tmp_path,
            bind_host="127.0.0.1",
            port=18100,
            allowed_origins=(),
            raw_token=RAW_TOKEN,
            owner_principal_id=OWNER_PRINCIPAL_ID,
        )
    assert not (tmp_path / "web" / "token").exists()
    assert not (tmp_path / "web" / "access.json").exists()

    monkeypatch.setattr(owner_auth, "_write_private_text", write_private_text)
    replacement = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
        raw_token=b"z" * 32,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    replacement.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Agent.Example.COM:443", "https://agent.example.com"),
        ("http://192.168.1.20:18100", "http://192.168.1.20:18100"),
        ("http://[fd00::1]:18100", "http://[fd00::1]:18100"),
    ],
)
def test_canonicalize_origin_normalizes_valid_values(raw: str, expected: str):
    assert canonicalize_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "https://agent.example.com/",
        "https://agent.example.com/path",
        "https://user@agent.example.com",
        "http://0.0.0.0:18100",
        "http://224.0.0.1:18100",
        "http://example.com:18100",
        "https://good.com\\evil",
        "https://good.com\x00.evil",
        "https://bad_host.example.com",
        "https://agent.example.com.",
        "ftp://agent.example.com",
    ],
)
def test_canonicalize_origin_rejects_unsafe_values(raw: str):
    with pytest.raises(OwnerAuthError):
        canonicalize_origin(raw)


@pytest.mark.parametrize(
    "host",
    ["https://127.0.0.1", "127.0.0.1:18100", "bad_host"],
)
def test_canonicalize_bind_host_rejects_url_and_port_syntax(host: str):
    with pytest.raises(OwnerAuthError):
        canonicalize_bind_host(host)


def test_non_loopback_bind_requires_explicit_origin():
    with pytest.raises(OwnerAuthError, match="allowed origin"):
        OwnerAuthState.from_raw_token(
            RAW_TOKEN,
            owner_principal_id=OWNER_PRINCIPAL_ID,
            bind_host="0.0.0.0",
            port=18100,
            allowed_origins=(),
        )


def test_empty_raw_token_is_rejected_instead_of_replaced():
    with pytest.raises(OwnerAuthError, match="exactly 32 bytes"):
        OwnerAuthState.from_raw_token(
            b"",
            owner_principal_id=OWNER_PRINCIPAL_ID,
            bind_host="127.0.0.1",
            port=18100,
            allowed_origins=(),
        )


def test_profile_owner_principals_produce_distinct_cookie_names():
    first = _state()
    second = OwnerAuthState.from_raw_token(
        RAW_TOKEN,
        owner_principal_id="owner/install/fedcba9876543210",
        bind_host="127.0.0.1",
        port=18101,
        allowed_origins=(),
    )

    assert first.cookie_name != second.cookie_name


def test_rotated_process_token_invalidates_prior_bearer_and_cookie():
    previous = _state()
    current = OwnerAuthState.from_raw_token(
        b"z" * 32,
        owner_principal_id=OWNER_PRINCIPAL_ID,
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
    )
    with TestClient(_app(current), base_url=LOCAL_ORIGIN) as client:
        old_bearer = client.get("/api/x", headers=_bearer(previous))
        assert old_bearer.status_code == 401
        old_cookie = client.get(
            "/api/x",
            headers={
                "cookie": (
                    f"{previous.cookie_name}={previous.cookie_value}"
                )
            },
        )
        assert old_cookie.status_code == 401


def test_public_shell_and_minimal_health_are_host_guarded():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        shell = client.get("/")
        assert shell.status_code == 200
        assert shell.headers["x-frame-options"] == "DENY"
        assert shell.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in shell.headers["content-security-policy"]
        assert "'sha256-" in shell.headers["content-security-policy"]
        assert "'unsafe-inline'" not in shell.headers["content-security-policy"]
        assert client.get("/healthz").json() == {"status": "ok"}
        rejected = client.get("/", headers={"host": "evil.example"})
        assert rejected.status_code == 403
        assert rejected.json() == {"error": "request_origin_rejected"}


def test_scheduler_shell_is_public_before_cookie_bootstrap():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        # The fixture has no Scheduler route, so 404 proves auth middleware
        # allowed the frontend shell path instead of returning 401.
        assert client.get("/scheduler").status_code == 404


def test_static_docs_are_public_but_keep_host_and_method_guards():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        # The fixture has no docs mount, so 404 proves the auth middleware
        # allowed the static docs paths instead of returning 401.
        assert client.get("/docs").status_code == 404
        assert client.head("/docs/reference/design/test.html").status_code == 404
        assert client.get("/docs/assets/site.js").status_code == 404
        assert client.post("/docs").status_code == 401
        assert client.get("/docs", headers={"host": "evil.example"}).status_code == 403


def test_public_docs_requests_only_read_prebuilt_files(tmp_path, monkeypatch):
    site = tmp_path / "site"
    nested = site / "assets"
    nested.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Docs</h1>", encoding="utf-8")
    (nested / "site.js").write_text("window.docs = true", encoding="utf-8")
    rebuild_checks = []
    monkeypatch.setattr(docs_route, "_site_dir", lambda: site)
    monkeypatch.setattr(docs_route, "_maybe_rebuild", lambda: rebuild_checks.append("startup"))

    app = FastAPI(docs_url=None, redoc_url=None)
    docs_route.register(app)
    assert rebuild_checks == ["startup"]
    protected = OwnerAuthMiddleware(app, auth_state=_state())
    with TestClient(protected, base_url=LOCAL_ORIGIN) as client:
        get_root = client.get("/docs", follow_redirects=False)
        assert get_root.status_code == 307
        assert get_root.headers["location"] == "/docs/"
        head_root = client.head("/docs", follow_redirects=False)
        assert head_root.status_code == 307
        assert head_root.headers["location"] == "/docs/"
        assert client.get("/docs/").status_code == 200
        assert client.head("/docs/assets/site.js").status_code == 200
        assert client.post("/docs").status_code == 401
        assert client.get("/docs", headers={"host": "evil.example"}).status_code == 403
    assert rebuild_checks == ["startup"]


def test_installed_package_prefers_bundled_docs(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    source = checkout / "docs" / "_site"
    bundled = (
        tmp_path
        / "site-packages"
        / "openprogram_server"
        / "_webui"
        / "_frontend"
        / "docs"
    )
    source.mkdir(parents=True)
    bundled.mkdir(parents=True)
    (source / "index.html").write_text("source", encoding="utf-8")
    (bundled / "index.html").write_text("bundled", encoding="utf-8")
    package_json = checkout / "apps" / "web" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(docs_route, "_repo_root", lambda: checkout)
    monkeypatch.setattr(docs_route, "_packaged_site_dir", lambda: bundled)
    assert docs_route._site_dir() == source

    package_json.unlink()
    assert docs_route._site_dir() == bundled


def test_public_challenge_proves_listener_ownership_without_receiving_token():
    state = _state()
    nonce = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        response = client.get(f"/api/auth/challenge?nonce={nonce}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "proof": create_owner_challenge_proof(
                token=state.token,
                nonce=nonce,
            )
        }
        assert state.token not in response.text


def test_public_challenge_can_bind_proof_to_the_running_revision(monkeypatch):
    state = _state()
    nonce = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    revision = "a" * 40
    monkeypatch.setattr("openprogram.webui.routes.misc._HEAD_SHA", revision)
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        response = client.get(
            f"/api/auth/challenge?nonce={nonce}&revision={revision}"
        )
        assert response.json() == {
            "proof": create_owner_challenge_proof(
                token=state.token,
                nonce=nonce,
                revision=revision,
            )
        }
        mismatch = client.get(
            f"/api/auth/challenge?nonce={nonce}&revision={'b' * 40}"
        )
        assert mismatch.status_code == 400
        assert mismatch.json() == {"error": "invalid_challenge"}


def test_bearer_authenticates_http_without_origin_and_never_caches():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        missing = client.get("/api/x")
        assert missing.status_code == 401
        assert missing.json() == {"error": "authentication_required"}
        assert missing.headers["www-authenticate"] == 'Bearer realm="OpenProgram"'
        query_token = client.get(f"/api/x?token={state.token}")
        assert query_token.status_code == 401
        assert state.token not in query_token.text
        accepted = client.get("/api/x", headers=_bearer(state))
        assert accepted.json() == {"tier": "owner"}
        assert state.authority["principal_id"] == OWNER_PRINCIPAL_ID
        assert accepted.headers["cache-control"] == "no-store"


def test_sse_uses_the_same_bearer_policy_and_no_store_header():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        assert client.get("/api/events").status_code == 401
        response = client.get("/api/events", headers=_bearer(state))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.content == b"data: ready\n\n"


def test_bearer_scheme_is_case_insensitive_and_future_routes_default_protected():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        assert client.get("/future-route").status_code == 401
        response = client.get(
            "/api/x",
            headers={"authorization": f"bearer {state.token}"},
        )
        assert response.status_code == 200


def test_duplicate_cookie_headers_return_generic_unauthorized():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        response = client.get(
            "/api/x",
            headers=[("cookie", "a=1"), ("cookie", "b=2")],
        )
        assert response.status_code == 401
        assert response.json() == {"error": "authentication_required"}


def test_bootstrap_sets_cookie_and_cookie_mutation_requires_exact_origin():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        response = client.post(
            "/api/auth/bootstrap",
            headers={"origin": LOCAL_ORIGIN},
            json={"token": state.token},
        )
        assert response.status_code == 204
        assert response.headers["cache-control"] == "no-store"
        cookie = response.headers["set-cookie"]
        assert state.cookie_name in cookie
        assert "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()
        assert "Domain=" not in cookie

        assert client.get("/api/x").json() == {"tier": "owner"}
        rejected = client.post("/api/x")
        assert rejected.status_code == 403
        assert rejected.json() == {"error": "request_origin_rejected"}
        accepted = client.post("/api/x", headers={"origin": LOCAL_ORIGIN})
        assert accepted.json() == {"tier": "owner"}


def test_bootstrap_rejects_bad_shapes_without_distinguishing_token_failures():
    state = _state()
    common = {"origin": LOCAL_ORIGIN, "content-type": "application/json"}
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        bodies = [
            b'{}',
            b'{"token":"wrong"}',
            ('{"token":"%s","extra":1}' % state.token).encode(),
            ('{"token":"%s","token":"%s"}' % (state.token, state.token)).encode(),
            b"x" * 257,
        ]
        for body in bodies:
            response = client.post(
                "/api/auth/bootstrap",
                headers=common,
                content=body,
            )
            assert response.status_code == 401
            assert response.json() == {"error": "authentication_required"}


def test_authorization_header_never_falls_back_to_valid_cookie():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        client.post(
            "/api/auth/bootstrap",
            headers={"origin": LOCAL_ORIGIN},
            json={"token": state.token},
        )
        response = client.get(
            "/api/x",
            headers={"authorization": "Basic ignored"},
        )
        assert response.status_code == 401


def test_websocket_requires_bearer_or_same_origin_cookie_before_accept():
    state = _state()
    with TestClient(_app(state), base_url=LOCAL_ORIGIN) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass

        with client.websocket_connect(
            "/ws", headers={**_bearer(state), "host": "127.0.0.1:18100"}
        ) as ws:
            assert ws.receive_text() == "owner"

        client.post(
            "/api/auth/bootstrap",
            headers={"origin": LOCAL_ORIGIN},
            json={"token": state.token},
        )
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws", headers={"host": "127.0.0.1:18100"}
            ):
                pass
        with client.websocket_connect(
            "/ws",
            headers={
                "host": "127.0.0.1:18100",
                "origin": LOCAL_ORIGIN,
                "cookie": f"{state.cookie_name}={state.cookie_value}",
            },
        ) as ws:
            assert ws.receive_text() == "owner"


def test_loopback_proxy_scheme_must_match_configured_https_origin():
    state = _state(origins=("https://agent.example.com",))
    with TestClient(
        _app(state),
        base_url="http://agent.example.com",
        client=("127.0.0.1", 50000),
    ) as client:
        accepted = client.get(
            "/api/x",
            headers={
                **_bearer(state),
                "host": "agent.example.com",
                "x-forwarded-proto": "https",
            },
        )
        assert accepted.status_code == 200
        rejected = client.get(
            "/api/x",
            headers={**_bearer(state), "host": "agent.example.com"},
        )
        assert rejected.status_code == 403


def test_non_loopback_peer_forwarded_scheme_headers_are_ignored():
    origin = "http://192.168.1.20:18100"
    state = _state(host="0.0.0.0", origins=(origin,))
    with TestClient(
        _app(state),
        base_url=origin,
        client=("192.168.1.20", 50000),
    ) as client:
        response = client.get(
            "/api/x",
            headers=[
                ("authorization", f"Bearer {state.token}"),
                ("host", "192.168.1.20:18100"),
                ("x-forwarded-proto", "https"),
                ("x-forwarded-proto", "invalid"),
            ],
        )
        assert response.status_code == 200


def test_create_app_enforces_owner_auth_and_public_health(monkeypatch):
    from openprogram.webui.server import create_app

    monkeypatch.setattr(
        "openprogram.webui.server._restore_sessions", lambda: None
    )
    state = _state()
    app = create_app(owner_auth=state, port=18100)
    assert app.state.owner_auth is state
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/diagnostics").status_code == 401
        detailed = client.get("/api/diagnostics", headers=_bearer(state))
        assert detailed.status_code == 200
        assert "uptime_seconds" in detailed.json()
        assert "revision" in detailed.json()
        assert detailed.json()["websocket_delivery"] == {
            "connections": 0,
            "managed_connections": 0,
            "queue_frames": 0,
            "queue_bytes": 0,
            "oldest_age": 0.0,
            "coalesced": 0,
            "dropped": 0,
            "send_failures": 0,
        }
        assert client.get("/api/providers/list").status_code == 401
        assert client.get(
            "/api/providers/list", headers=_bearer(state)
        ).status_code == 200
