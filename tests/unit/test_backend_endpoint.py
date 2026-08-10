"""Internal clients resolve one challenge-verified backend endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

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
    # Bracketed: the result is pasted into a URL authority, and bare
    # "::1:PORT" is not a parseable host:port.
    assert select_connect_host("::") == "[::1]"
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
    # Every URL is the same origin. A base_url whose Host disagreed with
    # the declared Origin is precisely what the owner-auth middleware
    # rejects as cross-origin, so the two may never drift apart.
    assert endpoint.base_url == f"http://localhost:{EPHEMERAL_PORT}"
    assert endpoint.websocket_url == f"ws://localhost:{EPHEMERAL_PORT}/ws"
    assert endpoint.origin == f"http://localhost:{EPHEMERAL_PORT}"
    assert endpoint.base_url == endpoint.origin
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
        origin=f"http://localhost:{EPHEMERAL_PORT}",
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
    # The dialled URL and the Host header agree, because both come from
    # the one canonical Origin.
    assert captured["url"] == f"http://localhost:{EPHEMERAL_PORT}/api/mcp/servers"
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


def test_endpoint_urls_are_all_one_origin_for_every_bind():
    """base_url / websocket_url / host can never disagree with origin.

    The TUI's 403 came from exactly this drift: a 127.0.0.1 base_url
    against a localhost Origin. Deriving all of them means no bind shape
    can reintroduce it.
    """
    from openprogram.backend_endpoint import BackendEndpoint

    for origin, ws in (
        (f"http://localhost:{EPHEMERAL_PORT}", f"ws://localhost:{EPHEMERAL_PORT}/ws"),
        (f"http://127.0.0.1:{EPHEMERAL_PORT}", f"ws://127.0.0.1:{EPHEMERAL_PORT}/ws"),
        (f"http://[::1]:{EPHEMERAL_PORT}", f"ws://[::1]:{EPHEMERAL_PORT}/ws"),
        ("https://agent.example.com:8443", "wss://agent.example.com:8443/ws"),
    ):
        endpoint = BackendEndpoint(origin=origin, token=TOKEN)
        assert endpoint.base_url == origin
        assert endpoint.websocket_url == ws
        assert endpoint.origin == origin
        # Host header == the authority actually dialled.
        assert endpoint.host == urlsplit(origin).netloc
        assert endpoint.scheme == urlsplit(origin).scheme
        assert endpoint.port == urlsplit(origin).port


def test_ipv6_wildcard_bind_yields_a_parseable_url():
    """``::`` must not produce ``http://::1:PORT``, which has no valid port."""
    host = select_connect_host("::")
    parsed = urlsplit(f"http://{host}:{EPHEMERAL_PORT}")
    assert parsed.port == EPHEMERAL_PORT
    assert parsed.hostname == "::1"


def test_resolve_backend_endpoint_fails_when_state_rotates_mid_read(
    monkeypatch,
    tmp_path: Path,
):
    """A restart between the snapshot and token reads must not half-apply.

    Otherwise the caller pairs the previous policy with the new token (or
    vice versa) and silently talks to the listener with a mismatched set.
    """
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    auth_state = _start_auth_state(tmp_path, allowed_origins=())
    monkeypatch.setattr(
        "openprogram._ports.backend_accepts_owner_challenge",
        lambda port, **_kwargs: True,
    )
    try:
        # Rotate the on-disk state at the moment the token is read, the
        # window a concurrent `openprogram web` restart would occupy.
        import openprogram.backend_endpoint as be

        real_read_token = be.read_web_token

        def rotate_then_read(*args, **kwargs):
            token = real_read_token(*args, **kwargs)
            replacement = OwnerAuthState.from_raw_token(
                b"z" * 32,
                owner_principal_id=OWNER_PRINCIPAL_ID,
                bind_host="127.0.0.1",
                port=EPHEMERAL_PORT + 1,
                allowed_origins=(),
            )
            (tmp_path / "web" / "access.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "bind_host": replacement.bind_host,
                        "port": replacement.port,
                        "effective_origins": sorted(replacement.effective_origins),
                        "token_fingerprint": replacement.fingerprint,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="ascii",
            )
            return token

        monkeypatch.setattr(be, "read_web_token", rotate_then_read)
        # Either guard is a correct refusal: the snapshot/token fingerprint
        # cross-check fires first here, and the re-read below catches a
        # rotation that kept the files consistent with each other.
        with pytest.raises(
            OwnerAuthError,
            match="do not match|changed while resolving",
        ):
            resolve_backend_endpoint(tmp_path)
    finally:
        auth_state.close()


def test_browser_url_follows_the_bind_instead_of_assuming_localhost(
    monkeypatch,
    tmp_path: Path,
):
    """A LAN/VPN bind must not be advertised as http://localhost:PORT.

    localhost is not an effective Origin for a 0.0.0.0 bind with an
    explicit allowed origin, so printing it sends the user to a dead page
    and minting a token URL for it raises outright.
    """
    from openprogram._cli_cmds import web as web_cli

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    lan_origin = f"http://192.168.1.20:{EPHEMERAL_PORT}"
    auth_state = _start_auth_state(
        tmp_path, bind_host="0.0.0.0", allowed_origins=(lan_origin,)
    )
    try:
        assert web_cli._browser_url(EPHEMERAL_PORT) == lan_origin
        assert (
            web_cli._browser_url(EPHEMERAL_PORT)
            in auth_state.effective_origins
        )
    finally:
        auth_state.close()

    # No snapshot to read (server not up) — localhost stays the fallback.
    assert web_cli._browser_url(EPHEMERAL_PORT) == f"http://localhost:{EPHEMERAL_PORT}"


def test_token_url_requires_the_target_origin_to_prove_itself(
    monkeypatch,
    tmp_path: Path,
):
    """The fragment token is readable by whatever page loads that URL.

    Proving some loopback listener is ours says nothing about a
    configured DNS/proxy Origin, so that exact URL must pass its own
    challenge before a token is minted for it.
    """
    from openprogram._cli_cmds import web as web_cli

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    external = "https://agent.example.com"
    auth_state = _start_auth_state(
        tmp_path, bind_host="0.0.0.0", allowed_origins=(external,)
    )
    monkeypatch.setattr(web_cli, "_backend_is_ours", lambda _port: True)
    try:
        challenged: list[str | None] = []

        def refuse(port, **kwargs):
            challenged.append(kwargs.get("origin"))
            return False

        monkeypatch.setattr(
            "openprogram._ports.backend_accepts_owner_challenge", refuse
        )
        with pytest.raises(OwnerAuthError, match="did not prove it is"):
            web_cli._active_owner_auth_url(external, EPHEMERAL_PORT)
        # It challenged the external URL itself, not merely the port.
        assert challenged == [external]

        monkeypatch.setattr(
            "openprogram._ports.backend_accepts_owner_challenge",
            lambda port, **_kwargs: True,
        )
        url = web_cli._active_owner_auth_url(external, EPHEMERAL_PORT)
        assert url == f"{external}/#token={auth_state.token}"
    finally:
        auth_state.close()


def test_mcp_detail_never_prints_env_values():
    """`openprogram mcp show` must not dump API keys to the terminal.

    A server's env is where credentials live; the rendered detail keeps
    the key names (which the user needs) and masks the values.
    """
    from openprogram._cli_cmds import mcp as mcp_cli

    secret = "sk-live-01234567890123456789"
    rendered = mcp_cli._render_detail({
        "name": "github",
        "ready": True,
        "error": "",
        "type": "stdio",
        "command": ["mcp-github"],
        "env": {"GITHUB_TOKEN": secret, "SHORT": "x"},
        "enabled": True,
        "timeout_seconds": 30,
        "tool_count": 0,
    })
    assert secret not in rendered
    assert "01234567890123456789" not in rendered
    # The names still show, so the config remains inspectable.
    assert "GITHUB_TOKEN" in rendered
    assert "SHORT" in rendered
    assert mcp_cli._render_detail({
        "name": "x", "ready": True, "error": "", "type": "stdio",
        "command": [], "env": {}, "enabled": True,
        "timeout_seconds": 30, "tool_count": 0,
    }).count("(empty)") == 1
