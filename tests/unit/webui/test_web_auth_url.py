from __future__ import annotations

from openprogram.interfaces.cli.application import build_parser
from openprogram.interfaces.cli.commands.services.web import _cmd_web_auth_url
from openprogram.host.security.backend_endpoint import (
    ActiveWebAccess,
    OwnerAuthError,
    build_owner_auth_url,
    resolve_effective_origins,
)


TOKEN = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


def test_build_owner_auth_url_requires_an_effective_origin():
    origins = resolve_effective_origins(
        "127.0.0.1",
        18100,
        ("https://agent.example.com",),
    )

    assert build_owner_auth_url(
        "https://agent.example.com",
        token=TOKEN,
        effective_origins=origins,
    ) == f"https://agent.example.com/#token={TOKEN}"

    try:
        build_owner_auth_url(
            "HTTPS://Agent.Example.COM:443",
            token=TOKEN,
            effective_origins=origins,
        )
    except OwnerAuthError as exc:
        assert "canonical Origin" in str(exc)
    else:
        raise AssertionError("a non-canonical base URL was accepted")

    try:
        build_owner_auth_url(
            "http://localhost:18101",
            token=TOKEN,
            effective_origins=origins,
        )
    except OwnerAuthError as exc:
        assert "effective origin" in str(exc)
    else:
        raise AssertionError("an unlisted origin was accepted")


def test_web_auth_url_parser_uses_protocol_field_name():
    args = build_parser().parse_args([
        "web",
        "auth-url",
        "--base-url",
        "http://127.0.0.1:18100",
    ])

    assert args.web_verb == "auth-url"
    assert args.base_url == "http://127.0.0.1:18100"


def test_web_auth_url_command_prints_only_the_requested_bootstrap_url(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle.read_worker_port",
        lambda: 18100,
    )
    monkeypatch.setattr(
        "openprogram.interfaces.cli.commands.services.web._backend_is_ours",
        lambda _port: True,
    )
    monkeypatch.setattr(
        "openprogram.host.security.backend_endpoint.read_active_web_access",
        lambda: ActiveWebAccess(
            bind_host="127.0.0.1",
            port=18100,
            effective_origins=frozenset({"http://127.0.0.1:18100"}),
            token_fingerprint="sha256:test",
        ),
    )
    monkeypatch.setattr(
        "openprogram.host.security.backend_endpoint.read_web_token",
        lambda: TOKEN,
    )
    # The requested URL must prove it is this server before a token is
    # minted for it (the fragment is readable by whatever page loads).
    challenged: list[str | None] = []

    def accept(port, **kwargs):
        challenged.append(kwargs.get("origin"))
        return True

    monkeypatch.setattr(
        "openprogram.operations.worker.ports.backend_accepts_owner_challenge", accept
    )

    assert _cmd_web_auth_url("http://127.0.0.1:18100") == 0
    assert challenged == ["http://127.0.0.1:18100"]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"http://127.0.0.1:18100/#token={TOKEN}\n"


def test_web_auth_url_command_fails_without_an_active_server(monkeypatch, capsys):
    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle.read_worker_port",
        lambda: None,
    )
    monkeypatch.setattr(
        "openprogram.interfaces.cli.commands.services.web._port_in_use", lambda _port: False,
    )

    assert _cmd_web_auth_url("http://127.0.0.1:18100") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: no active OpenProgram Web server\n"


def test_web_auth_url_does_not_disclose_token_to_an_unowned_port(
    monkeypatch,
    capsys,
):
    def fail_if_token_is_read():
        raise AssertionError("token was read")

    monkeypatch.setattr(
        "openprogram.operations.worker.lifecycle.read_worker_port",
        lambda: 18100,
    )
    monkeypatch.setattr(
        "openprogram.interfaces.cli.commands.services.web._backend_is_ours",
        lambda _port: False,
    )
    monkeypatch.setattr(
        "openprogram.host.security.backend_endpoint.read_web_token",
        fail_if_token_is_read,
    )

    assert _cmd_web_auth_url("http://127.0.0.1:18100") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error: active Web server is not owned by this profile\n"
    )


def test_web_start_that_never_binds_says_how_to_open_the_page_signed_in(monkeypatch, capsys):
    # A slow first start must not end in a page opened without its sign-in
    # link: that page loads with every list empty.
    from openprogram.interfaces.cli.commands.services import web

    opened: list[str] = []
    monkeypatch.setattr(web, "_port_in_use", lambda _port: False)
    monkeypatch.setattr("openprogram.operations.worker.spawn_detached", lambda: 0)
    # The worker names the port it settled on as it starts.
    monkeypatch.setattr("openprogram.operations.worker.lifecycle.read_worker_port", lambda: 18100)
    waits: list[float] = []
    monkeypatch.setattr("openprogram.interfaces.cli.ink._wait_until_listening", lambda port, timeout: waits.append(timeout) or False)
    monkeypatch.setattr("openprogram.host._platform.environment.tui_worker_ready_timeout_seconds", lambda: 120.0)
    monkeypatch.setattr("openprogram.host._platform.environment.open_browser_url", lambda url: opened.append(url) or True)

    web._cmd_web(18100, True)

    assert opened == []
    out = capsys.readouterr().out
    assert "still starting" in out
    assert "Run `openprogram web` again" in out
    # As long as the TUI waits: a first Windows start runs under Defender's scan.
    assert len(waits) == 1 and 119.0 < waits[0] <= 120.0
