from __future__ import annotations

from openprogram.cli import build_parser
from openprogram._cli_cmds.web import _cmd_web_auth_url
from openprogram.backend_endpoint import (
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
        "openprogram.worker.lifecycle.read_worker_port",
        lambda: 18100,
    )
    monkeypatch.setattr(
        "openprogram._cli_cmds.web._backend_is_ours",
        lambda _port: True,
    )
    monkeypatch.setattr(
        "openprogram.backend_endpoint.read_active_web_access",
        lambda: ActiveWebAccess(
            bind_host="127.0.0.1",
            port=18100,
            effective_origins=frozenset({"http://127.0.0.1:18100"}),
            token_fingerprint="sha256:test",
        ),
    )
    monkeypatch.setattr(
        "openprogram.backend_endpoint.read_web_token",
        lambda: TOKEN,
    )
    # The requested URL must prove it is this server before a token is
    # minted for it (the fragment is readable by whatever page loads).
    challenged: list[str | None] = []

    def accept(port, **kwargs):
        challenged.append(kwargs.get("origin"))
        return True

    monkeypatch.setattr(
        "openprogram._ports.backend_accepts_owner_challenge", accept
    )

    assert _cmd_web_auth_url("http://127.0.0.1:18100") == 0
    assert challenged == ["http://127.0.0.1:18100"]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"http://127.0.0.1:18100/#token={TOKEN}\n"


def test_web_auth_url_command_fails_without_an_active_server(monkeypatch, capsys):
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.read_worker_port",
        lambda: None,
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
        "openprogram.worker.lifecycle.read_worker_port",
        lambda: 18100,
    )
    monkeypatch.setattr(
        "openprogram._cli_cmds.web._backend_is_ours",
        lambda _port: False,
    )
    monkeypatch.setattr(
        "openprogram.backend_endpoint.read_web_token",
        fail_if_token_is_read,
    )

    assert _cmd_web_auth_url("http://127.0.0.1:18100") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error: active Web server is not owned by this profile\n"
    )
