"""The ssh client on Windows: no shared connection, no console, UTF-8 text."""
from __future__ import annotations

import types

import pytest

from openprogram.host.backend import ssh


@pytest.fixture
def launched(monkeypatch):
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        out = b"" if "text" not in kwargs else ""
        return types.SimpleNamespace(returncode=0, stdout=out, stderr=out)

    monkeypatch.setattr(ssh.subprocess, "run", run)
    monkeypatch.setattr(ssh, "no_window_creation_flags", lambda: 0x08000000)
    return calls


def test_remote_text_is_utf8_and_no_console_opens(launched):
    backend = ssh.SshBackend("me@devbox")
    backend.run("cat 说明.txt", timeout=5)
    backend.run_bytes("cat file", timeout=5)
    text, raw = launched
    assert (text["encoding"], text["errors"]) == ("utf-8", "replace")
    assert text["creationflags"] == 0x08000000 and raw["creationflags"] == 0x08000000
    assert "encoding" not in raw
    assert backend.spawn_spec("tail -f log")["creationflags"] == 0x08000000


def test_windows_client_gets_no_connection_sharing(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh, "_control_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ssh.os, "name", "nt")
    assert not any("Control" in item for item in ssh.SshBackend("h")._options())
    monkeypatch.setattr(ssh.os, "name", "posix")
    assert f"ControlPath={tmp_path}/%C" in ssh.SshBackend("h")._options()
