from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprogram import _compat
from openprogram.store.session.git_session import atomic_write_text


def test_filesystem_path_preserves_short_windows_paths(tmp_path: Path) -> None:
    assert _compat.filesystem_path(tmp_path) == str(tmp_path.absolute())


def test_atomic_write_preserves_utf8_and_lf_bytes(tmp_path: Path) -> None:
    target = tmp_path / "archive.txt"
    body = "first\n中文\n"
    atomic_write_text(target, body)
    assert target.read_bytes() == body.encode("utf-8")


def test_atomic_write_can_be_read_as_user_state(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, '{"ready": true}\n')
    assert _compat.read_user_state_bytes(target, limit=100) == b'{"ready": true}\n'


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended paths only")
def test_atomic_write_supports_long_path_without_system_policy(tmp_path: Path) -> None:
    parent = tmp_path / ("workflow-" + "x" * 180)
    os.makedirs(_compat.filesystem_path(parent))
    target = parent / ("candidate-" + "y" * 80 + ".json")
    assert len(str(target)) > 260

    atomic_write_text(target, '{"ok": true}\n')

    native = _compat.filesystem_path(target)
    with open(native, encoding="utf-8") as stream:
        assert stream.read() == '{"ok": true}\n'
    os.unlink(native)
