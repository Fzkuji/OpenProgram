"""A state file that cannot be read is unknown, never empty.

Every atomic write keeps the JSON file it replaces as ``<file>.bak``; a
config or credential file that is not valid JSON reads as that copy, the
unreadable bytes are set aside, and a read-modify-write builds on the
copy instead of on ``{}``.
"""

from __future__ import annotations

import json

import pytest

from openprogram.models.auth.credentials.io import _atomic_write
from openprogram.state import config as user_config
from openprogram.state import last_good


def _write(path, document) -> None:
    _atomic_write(path, lambda handle: handle.write(json.dumps(document).encode()))


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "get_config_path", lambda: path)
    return path


def test_each_write_keeps_the_file_it_replaces(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"generation": 1})
    assert not last_good.backup_path(path).exists()
    _write(path, {"generation": 2})
    assert json.loads(last_good.backup_path(path).read_text()) == {"generation": 1}


def test_an_unreadable_file_is_never_kept_as_the_good_copy(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"generation": 1})
    _write(path, {"generation": 2})
    path.write_bytes(b"\0" * 64)
    _write(path, {"generation": 3})
    assert json.loads(last_good.backup_path(path).read_text()) == {"generation": 1}


def test_a_file_of_another_kind_gets_no_copy(tmp_path):
    path = tmp_path / "notes.txt"
    _atomic_write(path, lambda handle: handle.write(b"one"))
    _atomic_write(path, lambda handle: handle.write(b"two"))
    assert not last_good.backup_path(path).exists()


@pytest.mark.parametrize("damage", [b"\0" * 128, b'{"providers": {"openai"', b"", b"   \n"])
def test_a_damaged_config_reads_as_its_last_good_copy(config_path, damage):
    user_config.write_config({"providers": {"openai": {"enabled": True}}})
    user_config.write_config({"providers": {"openai": {"enabled": True}}, "theme": "dark"})
    config_path.write_bytes(damage)

    assert user_config.read_config() == {"providers": {"openai": {"enabled": True}}}
    aside = list(config_path.parent.glob("config.json.bad-*"))
    if damage:
        assert [p.read_bytes() for p in aside] == [damage]


def test_an_update_builds_on_the_good_copy_not_on_nothing(config_path):
    user_config.write_config({"providers": {"openai": {"enabled": True}}})
    user_config.write_config({"providers": {"openai": {"enabled": True}}})
    config_path.write_bytes(b"\0" * 32)

    user_config.update_config(lambda cfg: cfg.__setitem__("theme", "dark"))

    assert json.loads(config_path.read_text()) == {
        "providers": {"openai": {"enabled": True}},
        "theme": "dark",
    }


def test_unreadable_bytes_are_set_aside_once(config_path):
    user_config.write_config({"a": 1})
    config_path.write_bytes(b"\0" * 16)
    user_config.read_config()
    user_config.read_config()
    assert len(list(config_path.parent.glob("config.json.bad-*"))) == 1


def test_damage_with_no_good_copy_reads_as_nothing_and_keeps_the_bytes(config_path):
    config_path.write_bytes(b"\0" * 16)
    assert user_config.read_config() == {}
    assert len(list(config_path.parent.glob("config.json.bad-*"))) == 1


def test_backups_leave_recovery_copies_out():
    from pathlib import Path

    from openprogram.operations.backup import _excluded

    assert _excluded(Path("/state/config.json.bak"))
    assert _excluded(Path("/state/config.json.bad-20261108T000000Z-abc"))
    assert not _excluded(Path("/state/config.json"))
