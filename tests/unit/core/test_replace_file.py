"""Publishing a state file waits out the moment Windows holds its destination.

Windows refuses ``os.replace`` while another handle has the destination
open: a reader in another thread or process, the indexer, an antivirus scan.
The refusal lasts milliseconds. These tests name the platform through the
seam's own ``_sys`` and stand a refusing ``os.replace`` in for the system
call, so they run here; they were not run on Windows.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from openprogram.host._platform import filesystem
from openprogram.state import config as user_config


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "The process cannot access the file because it is being used by another process")
    error.winerror = 32
    return error


@pytest.fixture
def held_destination(monkeypatch):
    """``os.replace`` refuses each destination twice, as when a reader holds
    it, then works."""
    real = os.replace
    calls: list[str] = []

    def replace(source, target, *args, **kwargs):
        calls.append(os.fspath(target))
        if calls.count(os.fspath(target)) <= 2:
            raise _sharing_violation()
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return calls


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(filesystem, "_sys", SimpleNamespace(platform="win32"))


def test_replace_waits_for_a_held_destination_on_windows(tmp_path, windows, held_destination):
    source, target = tmp_path / "new", tmp_path / "state.json"
    source.write_bytes(b"new")
    target.write_bytes(b"old")

    filesystem.replace_file(source, target)

    assert target.read_bytes() == b"new"
    assert len(held_destination) == 3


def test_replace_gives_up_after_its_bound(tmp_path, windows, monkeypatch):
    monkeypatch.setattr(filesystem, "_SHARING_RETRY_SECONDS", 0.02)
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(_sharing_violation()))
    source = tmp_path / "new"
    source.write_bytes(b"new")

    with pytest.raises(PermissionError):
        filesystem.replace_file(source, tmp_path / "state.json")


def test_posix_reports_a_refusal_at_once(tmp_path, held_destination):
    source = tmp_path / "new"
    source.write_bytes(b"new")

    with pytest.raises(PermissionError):
        filesystem.replace_file(source, tmp_path / "state.json")
    assert len(held_destination) == 1


def test_other_errors_are_not_retried(tmp_path, windows):
    with pytest.raises(FileNotFoundError):
        filesystem.replace_file(tmp_path / "missing", tmp_path / "state.json")


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "get_config_path", lambda: path)
    return path


def test_a_config_save_survives_a_reader_holding_the_file(config_path, windows, held_destination):
    config_path.write_text(json.dumps({"kept": 1}), encoding="utf-8")

    user_config.update_config(lambda cfg: cfg.__setitem__("added", 2))

    assert json.loads(config_path.read_bytes()) == {"kept": 1, "added": 2}


def test_a_config_read_survives_a_writer_replacing_the_file(config_path, windows, monkeypatch):
    config_path.write_text(json.dumps({"kept": 1}), encoding="utf-8")
    real = type(config_path).read_bytes
    attempts = []

    def read_bytes(self):
        attempts.append(self)
        if len(attempts) == 1:
            raise _sharing_violation()
        return real(self)

    monkeypatch.setattr(type(config_path), "read_bytes", read_bytes)

    assert user_config.read_config() == {"kept": 1}


def test_web_access_metadata_survives_a_reader_holding_the_file(tmp_path, windows, held_destination):
    from openprogram.interfaces.server import owner_auth

    path = tmp_path / "web" / "access.json"
    path.parent.mkdir()
    path.write_text("old", encoding="ascii")

    owner_auth._write_private_text(path, "new")

    assert path.read_text(encoding="ascii") == "new"
    assert held_destination.count(str(path)) == 3


def test_always_on_state_survives_a_reader_holding_the_file(tmp_path, windows, held_destination):
    from openprogram.runtime.conversation.always_on import store

    path = tmp_path / "always_on.json"
    path.write_text("{}", encoding="utf-8")

    store._replace_file(path, lambda handle: handle.write('{"note": "中文"}'))

    assert json.loads(path.read_bytes()) == {"note": "中文"}
    assert list(tmp_path.iterdir()) == [path]
