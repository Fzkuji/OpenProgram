"""Internal clients resolve one challenge-verified backend endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.backend_endpoint import (
    ActiveWebAccess,
    OwnerAuthError,
    resolve_backend_endpoint,
    select_connect_host,
    select_request_origin,
)
from openprogram.webui.owner_auth import OwnerAuthState


RAW_TOKEN = bytes(range(32))
TOKEN = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
OWNER_PRINCIPAL_ID = "owner/install/0123456789abcdef"
EPHEMERAL_PORT = 45231


def _start_auth_state(tmp_path: Path, bind_host: str = "127.0.0.1", **kwargs):
    return OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host=bind_host,
        port=EPHEMERAL_PORT,
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
        **kwargs,
    )


def test_select_connect_host_maps_wildcard_binds_onto_loopback():
    assert select_connect_host("0.0.0.0") == "127.0.0.1"
    assert select_connect_host("::") == "::1"
    assert select_connect_host("::1") == "[::1]"
    assert select_connect_host("127.0.0.1") == "127.0.0.1"
    assert select_connect_host("agent.example.com") == "agent.example.com"


def test_select_request_origin_prefers_localhost_then_the_bind_literal():
    loopback = ActiveWebAccess(
        bind_host="127.0.0.1",
        port=EPHEMERAL_PORT,
        effective_origins=frozenset({
            f"http://localhost:{EPHEMERAL_PORT}",
            f"http://127.0.0.1:{EPHEMERAL_PORT}",
        }),
        token_fingerprint="sha256:deadbeefcafe",
    )
    assert select_request_origin(loopback) == f"http://localhost:{EPHEMERAL_PORT}"

    remote = ActiveWebAccess(
        bind_host="10.1.2.3",
        port=EPHEMERAL_PORT,
        effective_origins=frozenset({"https://agent.example.com"}),
        token_fingerprint="sha256:deadbeefcafe",
    )
    assert select_request_origin(remote) == "https://agent.example.com"


def test_resolve_backend_endpoint_requires_a_verified_challenge(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    auth_state = _start_auth_state(tmp_path, allowed_origins=())
    try:
        monkeypatch.setattr(
            "openprogram._ports.backend_accepts_owner_challenge",
            lambda port, **_kwargs: False,
        )
        with pytest.raises(OwnerAuthError, match="not owned by this profile"):
            resolve_backend_endpoint(tmp_path)

        seen: list[int] = []

        def accept(port, **_kwargs):
            seen.append(port)
            return True

        monkeypatch.setattr(
            "openprogram._ports.backend_accepts_owner_challenge", accept
        )
        endpoint = resolve_backend_endpoint(tmp_path)
    finally:
        auth_state.close()

    assert seen == [EPHEMERAL_PORT]
    assert endpoint.base_url == f"http://127.0.0.1:{EPHEMERAL_PORT}"
    assert endpoint.websocket_url == f"ws://127.0.0.1:{EPHEMERAL_PORT}/ws"
    assert endpoint.origin == f"http://localhost:{EPHEMERAL_PORT}"
    assert endpoint.host == f"localhost:{EPHEMERAL_PORT}"
    assert endpoint.scheme == "http"
    assert endpoint.port == EPHEMERAL_PORT
    assert endpoint.token == TOKEN
    assert endpoint.authorization_header == f"Bearer {TOKEN}"
    # The token must not leak through the URL or a debug repr.
    assert TOKEN not in endpoint.base_url
    assert TOKEN not in endpoint.websocket_url
    assert TOKEN not in repr(endpoint)


def test_resolve_backend_endpoint_never_transmits_the_token(
    monkeypatch,
    tmp_path: Path,
):
    """A stranger holding the port must never receive the owner credential.

    The only network call resolution makes is the challenge, which carries a
    random nonce and no credential; nothing else goes on the wire before the
    listener has proven it holds the same token.
    """
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    auth_state = _start_auth_state(tmp_path, allowed_origins=())
    sent: list[str] = []
    try:
        class RejectingOpener:
            def open(self, request, timeout):
                sent.append(request.full_url)
                for _name, value in request.header_items():
                    assert TOKEN not in value
                assert TOKEN not in request.full_url
                raise OSError("nothing is listening")

        monkeypatch.setattr(
            "urllib.request.build_opener", lambda *_handlers: RejectingOpener()
        )
        monkeypatch.setattr(
            "openprogram.worker.lifecycle.current_worker_pid", lambda: 12345
        )
        with pytest.raises(OwnerAuthError, match="not owned by this profile"):
            resolve_backend_endpoint(tmp_path)
    finally:
        auth_state.close()

    assert len(sent) == 1
    assert "/api/auth/challenge?" in sent[0]


def test_mcp_cli_sends_bearer_only_to_the_verified_endpoint(monkeypatch):
    """``openprogram mcp`` authenticates instead of assuming 127.0.0.1."""
    from openprogram._cli_cmds import mcp as mcp_cli
    from openprogram.backend_endpoint import BackendEndpoint

    endpoint = BackendEndpoint(
        base_url=f"http://127.0.0.1:{EPHEMERAL_PORT}",
        websocket_url=f"ws://127.0.0.1:{EPHEMERAL_PORT}/ws",
        origin=f"http://localhost:{EPHEMERAL_PORT}",
        host=f"localhost:{EPHEMERAL_PORT}",
        scheme="http",
        port=EPHEMERAL_PORT,
        token=TOKEN,
    )
    monkeypatch.setattr(
        "openprogram.backend_endpoint.resolve_backend_endpoint",
        lambda *_args, **_kwargs: endpoint,
    )

    captured: dict = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"servers":[]}'

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {
                name.lower(): value for name, value in request.header_items()
            }
            return Response()

    monkeypatch.setattr(
        "urllib.request.build_opener", lambda *_handlers: Opener()
    )

    status, payload = mcp_cli._request("GET", "/api/mcp/servers")

    assert status == 200
    assert payload == {"servers": []}
    assert captured["url"] == f"http://127.0.0.1:{EPHEMERAL_PORT}/api/mcp/servers"
    assert captured["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert captured["headers"]["host"] == f"localhost:{EPHEMERAL_PORT}"
    assert TOKEN not in captured["url"]


def test_mcp_cli_exits_when_no_verified_endpoint(monkeypatch, capsys):
    from openprogram._cli_cmds import mcp as mcp_cli

    def refuse(*_args, **_kwargs):
        raise OwnerAuthError("no active Web access snapshot")

    monkeypatch.setattr(
        "openprogram.backend_endpoint.resolve_backend_endpoint", refuse
    )
    with pytest.raises(SystemExit) as exit_info:
        mcp_cli._request("GET", "/api/mcp/servers")
    assert exit_info.value.code == 1
    assert "no active Web access snapshot" in capsys.readouterr().err
