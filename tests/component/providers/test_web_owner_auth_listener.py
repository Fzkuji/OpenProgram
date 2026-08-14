"""Acceptance tests against a REAL Uvicorn listener on an ephemeral port.

The sibling ``test_web_owner_auth.py`` drives the middleware through
Starlette's in-process TestClient, which never exercises the HTTP/1.1 and
WebSocket handshake wire format. These tests bind a real socket on port 0
so the assertions cover what a remote client actually sees: status lines,
response headers, the pre-accept WebSocket rejection, and the fact that
the owner token never appears on the wire, in a log, or in a rendered page.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute

from openprogram.backend_endpoint import OwnerAuthError
from openprogram.webui.owner_auth import (
    OwnerAuthMiddleware,
    OwnerAuthState,
)


RAW_TOKEN = bytes(range(32))
OWNER_PRINCIPAL_ID = "owner/install/0123456789abcdef"


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _app(state: OwnerAuthState) -> Starlette:
    async def shell(_request: Request):
        # A rendered page must not carry the token, even though the server
        # holds it — this is the surface a leak would most plausibly reach.
        return HTMLResponse("<script>window.openprogramReady=true</script>")

    async def api(request: Request):
        return JSONResponse(
            {"tier": request.scope["state"]["authority"]["authority_tier"]}
        )

    async def events(_request: Request):
        async def stream():
            yield b"data: ready\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def socket_route(ws):
        await ws.accept()
        await ws.send_text(ws.scope["state"]["authority"]["authority_tier"])
        await ws.close()

    routes = [
        Route("/", shell),
        Route("/api/x", api, methods=["GET", "POST"]),
        Route("/api/events", events),
        WebSocketRoute("/ws", socket_route),
    ]
    return OwnerAuthMiddleware(Starlette(routes=routes), auth_state=state)


@contextmanager
def _listener(state: OwnerAuthState, port: int, *, host: str = "127.0.0.1"):
    """Serve ``state``'s app on a real socket until the block exits."""
    import uvicorn

    config = uvicorn.Config(
        _app(state),
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve()), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.02)
    assert server.started, "uvicorn did not bind in time"
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


@pytest.fixture
def live(tmp_path: Path):
    """A started OwnerAuthState plus a real listener on an ephemeral port."""
    port = _free_port()
    state = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=(),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    try:
        with _listener(state, port) as base_url:
            yield state, base_url, port
    finally:
        state.close()


def test_real_listener_accepts_bearer_and_rejects_missing_credential(live):
    state, base_url, _ = live
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        accepted = client.get(
            "/api/x", headers={"Authorization": f"Bearer {state.token}"}
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"tier": "owner"}
        assert accepted.headers["cache-control"] == "no-store"

        rejected = client.get("/api/x")
        assert rejected.status_code == 401
        assert rejected.json() == {"error": "authentication_required"}
        assert rejected.headers["www-authenticate"] == 'Bearer realm="OpenProgram"'


def test_real_listener_sse_is_authenticated_and_never_cached(live):
    state, base_url, _ = live
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        assert client.get("/api/events").status_code == 401
        response = client.get(
            "/api/events", headers={"Authorization": f"Bearer {state.token}"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.content == b"data: ready\n\n"


def _raw_websocket_handshake(port: int, headers: dict[str, str]) -> str:
    """Do the WebSocket opening handshake by hand and return the raw reply.

    A client library hides the rejection status behind an exception, but the
    invariant under test is precisely what goes over the wire before accept.
    """
    request = [f"GET /ws HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    request += [
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    request += [f"{name}: {value}" for name, value in headers.items()]
    with closing(socket.create_connection(("127.0.0.1", port), timeout=10.0)) as sock:
        sock.sendall(("\r\n".join(request) + "\r\n\r\n").encode("ascii"))
        # Read only the response head: an accepted upgrade keeps the socket
        # open, so reading to EOF would block until the timeout.
        chunks = bytearray()
        while b"\r\n\r\n" not in chunks and len(chunks) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.extend(chunk)
        head, _, rest = bytes(chunks).partition(b"\r\n\r\n")
        fields = {
            name.strip().lower(): value.strip()
            for name, _, value in (
                line.partition(b":") for line in head.split(b"\r\n")[1:]
            )
        }
        length = int(fields.get(b"content-length", b"0") or 0)
        while len(rest) < length:
            chunk = sock.recv(4096)
            if not chunk:
                break
            rest += chunk
    return (head + b"\r\n\r\n" + rest).decode("latin-1")


def test_real_listener_websocket_is_rejected_before_accept_with_headers(live):
    state, _, port = live
    unauthenticated = _raw_websocket_handshake(port, {})
    assert unauthenticated.startswith("HTTP/1.1 401")
    assert "www-authenticate: bearer" in unauthenticated.lower()
    assert "cache-control: no-store" in unauthenticated.lower()
    assert '{"error":"authentication_required"}' in unauthenticated
    # Rejected before accept: no upgrade was ever negotiated.
    assert "101" not in unauthenticated.split("\r\n", 1)[0]

    accepted = _raw_websocket_handshake(
        port, {"Authorization": f"Bearer {state.token}"}
    )
    assert accepted.startswith("HTTP/1.1 101")


def test_bind_failure_leaves_no_token_or_snapshot_behind(tmp_path: Path):
    """A port collision after start must not strand owner credentials."""
    port = _free_port()
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        state = OwnerAuthState.start(
            state_dir=tmp_path,
            bind_host="127.0.0.1",
            port=port,
            allowed_origins=(),
            raw_token=RAW_TOKEN,
            owner_principal_id=OWNER_PRINCIPAL_ID,
        )
        assert (tmp_path / "web" / "token").exists()
        try:
            import uvicorn

            server = uvicorn.Server(
                uvicorn.Config(
                    _app(state),
                    host="127.0.0.1",
                    port=port,
                    log_level="critical",
                    access_log=False,
                )
            )
            with pytest.raises(SystemExit):
                asyncio.run(server.serve())
        finally:
            state.close()
    finally:
        blocker.close()

    assert not (tmp_path / "web" / "token").exists()
    assert not (tmp_path / "web" / "access.json").exists()
    # The lock is released too, so the next start can claim a fresh token.
    replacement = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=_free_port(),
        allowed_origins=(),
        raw_token=b"z" * 32,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    try:
        assert replacement.token != state.token
    finally:
        replacement.close()


def test_rotated_token_invalidates_old_cookie_and_bearer_on_the_wire(
    tmp_path: Path,
):
    port = _free_port()
    previous = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=(),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    old_token, old_cookie_name, old_cookie = (
        previous.token,
        previous.cookie_name,
        previous.cookie_value,
    )
    previous.close()

    rotated = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=(),
        raw_token=b"z" * 32,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    assert rotated.token != old_token
    assert rotated.cookie_name == old_cookie_name  # same profile, same name
    try:
        with _listener(rotated, port) as base_url, httpx.Client(
            base_url=base_url, timeout=10.0
        ) as client:
            assert client.get(
                "/api/x", headers={"Authorization": f"Bearer {old_token}"}
            ).status_code == 401
            assert client.get(
                "/api/x", headers={"Cookie": f"{old_cookie_name}={old_cookie}"}
            ).status_code == 401
            assert client.get(
                "/api/x", headers={"Authorization": f"Bearer {rotated.token}"}
            ).status_code == 200
    finally:
        rotated.close()


def test_two_profiles_do_not_accept_each_others_cookies(tmp_path: Path):
    """Distinct state dirs are distinct owners: no credential crosses over."""
    profiles = []
    for name, principal, raw in (
        ("a", "owner/install/0123456789abcdef", RAW_TOKEN),
        ("b", "owner/install/fedcba9876543210", b"z" * 32),
    ):
        port = _free_port()
        profiles.append((
            OwnerAuthState.start(
                state_dir=tmp_path / name,
                bind_host="127.0.0.1",
                port=port,
                allowed_origins=(),
                raw_token=raw,
                owner_principal_id=principal,
            ),
            port,
        ))
    (first, first_port), (second, second_port) = profiles
    assert first.cookie_name != second.cookie_name
    try:
        with _listener(first, first_port) as first_url, _listener(
            second, second_port
        ) as second_url:
            for state, url, other in (
                (first, first_url, second),
                (second, second_url, first),
            ):
                with httpx.Client(base_url=url, timeout=10.0) as client:
                    assert client.get(
                        "/api/x",
                        headers={
                            "Cookie": f"{other.cookie_name}={other.cookie_value}"
                        },
                    ).status_code == 401
                    assert client.get(
                        "/api/x",
                        headers={"Authorization": f"Bearer {other.token}"},
                    ).status_code == 401
                    assert client.get(
                        "/api/x",
                        headers={
                            "Cookie": f"{state.cookie_name}={state.cookie_value}"
                        },
                    ).status_code == 200
    finally:
        for state, _ in profiles:
            state.close()


def test_reverse_proxy_forwarded_headers_and_origin_matrix(tmp_path: Path):
    """External bind behind a TLS proxy: only the configured origin passes."""
    port = _free_port()
    state = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=("https://agent.example.com",),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    bearer = {"Authorization": f"Bearer {state.token}"}
    proxied = {
        "Host": "agent.example.com",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "agent.example.com",
    }
    try:
        with _listener(state, port) as base_url, httpx.Client(
            base_url=base_url, timeout=10.0
        ) as client:
            # Browser request through the proxy with the legitimate Origin.
            assert client.post(
                "/api/x",
                headers={
                    **proxied,
                    **bearer,
                    "Origin": "https://agent.example.com",
                },
            ).status_code == 200
            # A forged Origin from another site is refused.
            assert client.post(
                "/api/x",
                headers={**proxied, **bearer, "Origin": "https://evil.example"},
            ).status_code == 403
            # Non-browser client: no Origin at all, but a valid Bearer passes.
            assert client.post(
                "/api/x", headers={**proxied, **bearer}
            ).status_code == 200
            # X-Forwarded-Host alone cannot rewrite an unlisted Host.
            assert client.post(
                "/api/x",
                headers={
                    "Host": "evil.example",
                    "X-Forwarded-Host": "agent.example.com",
                    "X-Forwarded-Proto": "https",
                    **bearer,
                },
            ).status_code == 403
            # Dropping the forwarded scheme makes the origin http:// and
            # therefore not one of the effective origins.
            assert client.post(
                "/api/x",
                headers={"Host": "agent.example.com", **bearer},
            ).status_code == 403
    finally:
        state.close()


def test_startup_logs_warn_about_plaintext_http_for_remote_origins(
    monkeypatch, tmp_path: Path, capsys
):
    """A non-loopback http:// origin must be called out as unencrypted."""
    from openprogram.webui import server as webui_server

    port = _free_port()
    monkeypatch.setattr(webui_server, "_server_thread", None)
    monkeypatch.setattr(webui_server, "_restore_sessions", lambda: None)
    monkeypatch.setattr(
        webui_server,
        "_web_config",
        lambda: {
            "bind_host": "127.0.0.1",
            "allowed_origins": ("http://192.168.1.20:%d" % port,),
        },
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    # Keep the uvicorn boot out of this test: the assertion is on the banner
    # start_server prints after the auth state exists.
    monkeypatch.setattr(
        webui_server.threading, "Thread", lambda *a, **k: _NoopThread()
    )
    try:
        webui_server.start_server(port=port, open_browser=False)
        printed = capsys.readouterr().out
    finally:
        if webui_server._owner_auth_state is not None:
            webui_server._owner_auth_state.close()
        webui_server._owner_auth_state = None
        webui_server._server_thread = None

    assert "binding_scope=loopback" in printed
    assert "forwarded_proto_trust=loopback-peer-only" in printed
    assert "WARNING: owner token traffic uses unencrypted HTTP" in printed
    assert "http://192.168.1.20:%d" % port in printed
    assert "token_fingerprint=sha256:" in printed


class _NoopThread:
    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return False

    def join(self, timeout=None) -> None:
        return None


def test_token_never_appears_in_logs_responses_urls_or_rendered_html(
    live, caplog, capsys
):
    """One broad sweep: after real traffic, the token is nowhere observable."""
    state, base_url, port = live
    token = state.token
    # Capture everything the SERVER emits. The httpx/httpcore client loggers
    # are excluded on purpose: they echo back the request line this test
    # itself crafted with ``?token=``, which says nothing about the server.
    caplog.set_level("DEBUG")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        observed = [
            client.get("/"),
            client.get("/api/x", headers={"Authorization": f"Bearer {token}"}),
            client.get("/api/x"),
            client.get(f"/api/x?token={token}"),
            client.post(
                "/api/auth/bootstrap",
                headers={"Origin": base_url, "Content-Type": "application/json"},
                content=json.dumps({"token": token}),
            ),
            client.get("/api/auth/challenge?nonce=" + "A" * 43),
        ]

    # The query-string smuggling attempt must not authenticate either.
    assert observed[3].status_code == 401

    for response in observed:
        assert token not in response.text
        # Header values (Set-Cookie included) must carry a derived value only.
        for name, value in response.headers.items():
            assert token not in value, f"token leaked in header {name}"

    # The rendered shell HTML is token-free.
    assert observed[0].status_code == 200
    assert token not in observed[0].text

    # The cookie handed to the browser is a derivation, not the token.
    cookie = observed[4].headers.get("set-cookie", "")
    assert state.cookie_value in cookie
    assert token not in cookie

    # Logs (stdout/stderr and the logging framework) stay clean.
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err
    assert token not in caplog.text

    # And the state's own repr, the thing most likely to reach a traceback.
    assert token not in repr(state)


def test_snapshot_rejects_a_tampered_token_fingerprint(tmp_path: Path):
    """A snapshot that no longer matches the token file is not trusted."""
    from openprogram.backend_endpoint import read_active_web_access

    state = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=_free_port(),
        allowed_origins=(),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    try:
        assert read_active_web_access(tmp_path).port == state.port
        snapshot = tmp_path / "web" / "access.json"
        payload = json.loads(snapshot.read_text(encoding="ascii"))
        payload["token_fingerprint"] = "sha256:000000000000"
        snapshot.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="ascii",
        )
        with pytest.raises(OwnerAuthError, match="do not match"):
            read_active_web_access(tmp_path)
    finally:
        state.close()


# ---------------------------------------------------------------------------
# Real Node client against the real listener.
#
# Every test above drives the middleware from Python, which is why the
# TUI's 403 survived a green suite: the Node client reaches the loopback
# listener by IP (Host: 127.0.0.1:PORT, from BackendEndpoint.base_url) but
# presents the canonical effective Origin (http://localhost:PORT, from
# BackendEndpoint.origin). No Python test ever sent that combination, so
# nothing covered the one request shape the TUI actually makes. These
# tests spawn a real `node` and send it, over both HTTP and the WebSocket
# handshake, using the same header construction as cli/src.
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")
_CLI_DIR = Path(__file__).resolve().parents[3] / "cli"
requires_node = pytest.mark.skipif(
    _NODE is None or not (_CLI_DIR / "node_modules" / "ws").is_dir(),
    reason="node with cli/node_modules (for the real `ws` client) is required",
)


def _run_node(script: str, env_extra: dict[str, str]) -> dict:
    """Run ``script`` under the real node, in cli/ so `ws` resolves.

    The script prints one JSON object; we return it parsed. Written into
    cli/ rather than tmp_path because Node resolves bare imports against
    the directory tree of the *file*, so `import 'ws'` only works from
    inside the package that depends on it.
    """
    path = _CLI_DIR / f".auth_probe_{os.getpid()}_{threading.get_ident()}.mjs"
    path.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(
            [_NODE, str(path)],
            cwd=str(_CLI_DIR),
            env={**os.environ, **env_extra},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        path.unlink(missing_ok=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# Mirrors cli/src/utils/backend.ts backendAuthHeaders() + backendFetch(),
# and cli/src/ws/client.ts connect(): Authorization from the token env var,
# Origin from OPENPROGRAM_BACKEND_ORIGIN, dialling OPENPROGRAM_BACKEND_URL.
_PROBE = """
import WebSocket from 'ws';

const base = process.env.OPENPROGRAM_BACKEND_URL;
const headers = {
  Authorization: `Bearer ${process.env.OPENPROGRAM_BACKEND_TOKEN}`,
};
const origin = process.env.OPENPROGRAM_BACKEND_ORIGIN;
if (origin) headers.Origin = origin;

const out = {};
const post = await fetch(`${base}/api/x`, {
  method: 'POST',
  headers,
  redirect: 'error',
});
out.http_status = post.status;
out.http_body = await post.text();

const get = await fetch(`${base}/api/x`, { headers, redirect: 'error' });
out.get_status = get.status;

out.ws_status = await new Promise((resolve) => {
  const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws`, { headers });
  let status = 0;
  ws.on('upgrade', (m) => { status = m.statusCode; });
  ws.on('open', () => { ws.close(); resolve(status || 101); });
  ws.on('error', (e) => resolve(status || String(e)));
  setTimeout(() => resolve(status || 'timeout'), 15000);
});

console.log(JSON.stringify(out));
"""


@requires_node
def test_real_node_client_is_accepted_with_the_launcher_origin(live):
    """The exact shape cli_ink.py hands the TUI must authenticate.

    ``BackendEndpoint`` dials the loopback IP but carries the canonical
    ``http://localhost:PORT`` Origin, so Host and Origin are two spellings
    of the same server. A bearer request must be accepted anyway.
    """
    state, base_url, port = live
    result = _run_node(
        _PROBE,
        {
            "OPENPROGRAM_BACKEND_URL": base_url,
            "OPENPROGRAM_BACKEND_ORIGIN": f"http://localhost:{port}",
            "OPENPROGRAM_BACKEND_TOKEN": state.token,
        },
    )
    assert result["http_status"] == 200, result
    assert json.loads(result["http_body"]) == {"tier": "owner"}
    assert result["get_status"] == 200, result
    assert result["ws_status"] == 101, result


@requires_node
def test_real_node_client_is_accepted_when_origin_matches_the_host(live):
    """The other effective origin — dialled and declared identically."""
    state, base_url, port = live
    result = _run_node(
        _PROBE,
        {
            "OPENPROGRAM_BACKEND_URL": base_url,
            "OPENPROGRAM_BACKEND_ORIGIN": f"http://127.0.0.1:{port}",
            "OPENPROGRAM_BACKEND_TOKEN": state.token,
        },
    )
    assert result["http_status"] == 200, result
    assert result["ws_status"] == 101, result


@requires_node
def test_real_node_client_with_a_foreign_origin_is_still_rejected(live):
    """Relaxing Origin for bearer must not open a cross-origin hole.

    An Origin outside the effective set is refused even with a valid
    token, so a page on another site cannot drive the listener.
    """
    state, base_url, _ = live
    result = _run_node(
        _PROBE,
        {
            "OPENPROGRAM_BACKEND_URL": base_url,
            "OPENPROGRAM_BACKEND_ORIGIN": "http://evil.example.com",
            "OPENPROGRAM_BACKEND_TOKEN": state.token,
        },
    )
    assert result["http_status"] == 403, result
    assert json.loads(result["http_body"]) == {"error": "request_origin_rejected"}
    assert result["ws_status"] != 101, result


@requires_node
def test_real_node_client_with_a_bad_token_is_rejected(live):
    """A mismatched Origin is only tolerated for a token that verifies."""
    _state_unused, base_url, port = live
    result = _run_node(
        _PROBE,
        {
            "OPENPROGRAM_BACKEND_URL": base_url,
            "OPENPROGRAM_BACKEND_ORIGIN": f"http://localhost:{port}",
            "OPENPROGRAM_BACKEND_TOKEN": "f" * 43,
        },
    )
    assert result["http_status"] == 403, result
    assert result["ws_status"] != 101, result
