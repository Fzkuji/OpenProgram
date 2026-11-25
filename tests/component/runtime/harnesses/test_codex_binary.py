"""The Codex used is the one the user installed: the setting, else PATH."""
from openprogram.runtime.harnesses.codex import client


def _fake(path, version):
    path.write_text(f"#!/bin/sh\necho 'codex-cli {version}'\n")
    path.chmod(0o755)
    return str(path)


def test_codex_on_path_is_used(tmp_path, monkeypatch):
    found = _fake(tmp_path / "codex", "0.172.0")
    monkeypatch.delenv("OPENPROGRAM_CODEX_BINARY", raising=False)
    monkeypatch.setattr("openprogram.state.config.read_config_value", lambda _key: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert client.binary_path() == found
    assert client.binary_version(found) == "0.172.0"


def test_setting_overrides_path(tmp_path, monkeypatch):
    chosen = _fake(tmp_path / "mine", "0.1.0")
    monkeypatch.setenv("OPENPROGRAM_CODEX_BINARY", chosen)
    assert client.binary_path() == chosen


def test_nothing_installed_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENPROGRAM_CODEX_BINARY", raising=False)
    monkeypatch.setattr("openprogram.state.config.read_config_value", lambda _key: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("SHELL", raising=False)
    assert client.binary_path() is None
