from __future__ import annotations

import importlib
import sys

import pytest

from openprogram import cli
from openprogram._cli_cmds import mcp as mcp_commands


def _auth():
    return importlib.import_module("openprogram.mcp_server.auth")


def test_nested_token_create_parser_contract():
    args = cli.build_parser().parse_args(["mcp", "token", "create"])

    assert args.command == "mcp"
    assert args.mcp_verb == "token"
    assert args.mcp_token_verb == "create"


def test_nested_token_without_create_prints_local_help_and_exits_two(
    monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["openprogram", "mcp", "token"])

    with pytest.raises(SystemExit) as caught:
        cli.main()

    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "create" in output.out
    assert "Traceback" not in output.err


def test_token_create_dispatch_is_local_and_prints_new_token_once(
    tmp_path, monkeypatch, capsys
):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    monkeypatch.setattr(auth, "token_path", lambda: target)

    def backend_must_not_be_resolved():
        raise AssertionError("local token creation consulted backend endpoint")

    monkeypatch.setattr(
        mcp_commands, "_require_backend_endpoint", backend_must_not_be_resolved
    )
    monkeypatch.setattr(sys, "argv", ["openprogram", "mcp", "token", "create"])

    with pytest.raises(SystemExit) as caught:
        cli.main()

    assert caught.value.code == 0
    output = capsys.readouterr()
    stored = target.read_text(encoding="ascii")
    assert output.out == f"{stored}\n"
    assert output.err == ""
    assert output.out.count(stored) == 1


def test_token_create_existing_file_error_never_prints_existing_token(
    tmp_path, monkeypatch, capsys
):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    existing = "existing-secret-token"
    target.write_text(existing, encoding="ascii")
    target.chmod(0o600)
    monkeypatch.setattr(auth, "token_path", lambda: target)
    monkeypatch.setattr(sys, "argv", ["openprogram", "mcp", "token", "create"])

    with pytest.raises(SystemExit) as caught:
        cli.main()

    assert caught.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Error: MCP server token already exists\n"
    assert existing not in output.err


def test_token_create_failure_never_prints_generated_token(
    tmp_path, monkeypatch, capsys
):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    generated = "generated-secret-token"
    monkeypatch.setattr(auth, "token_path", lambda: target)
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda size: generated)

    def fail_publish(*_args, **_kwargs):
        raise OSError(f"publish failed for {generated}")

    monkeypatch.setattr(auth.os, "link", fail_publish)

    result = mcp_commands._cmd_mcp_token_create()

    assert result == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Error: could not create MCP server token\n"
    assert generated not in output.err


@pytest.mark.parametrize(
    "argv",
    [
        ["mcp", "list"],
        ["mcp", "show", "demo"],
        ["mcp", "add", "demo", "python", "server.py"],
        ["mcp", "rm", "demo"],
        ["mcp", "restart", "demo"],
        ["mcp", "enable", "demo"],
        ["mcp", "disable", "demo"],
        ["mcp", "edit"],
        ["mcp", "test", "demo", "python", "server.py"],
    ],
)
def test_existing_mcp_management_verbs_still_parse(argv):
    args = cli.build_parser().parse_args(argv)

    assert args.mcp_verb == argv[1]
