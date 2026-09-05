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


def test_regular_binary_read_preserves_all_bytes(tmp_path: Path) -> None:
    target = tmp_path / "package.bin"
    payload = b"line\r\n\x1a\x00\xff"
    target.write_bytes(payload)
    with _compat.open_regular_binary(target) as stream:
        assert stream.read() == payload


def test_regular_binary_read_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="regular file"):
        _compat.open_regular_binary(tmp_path)


def test_package_hash_uses_binary_bytes_and_size_bound(tmp_path: Path) -> None:
    import hashlib
    from openprogram.self_update.package_protocol import _read_or_hash

    target = tmp_path / "app.asar"
    payload = b"\r\n\x1a\xff"
    target.write_bytes(payload)
    assert _read_or_hash(target, limit=4) == hashlib.sha256(payload).hexdigest()
    assert _read_or_hash(target, limit=4, read=True) == payload
    with pytest.raises(ValueError):
        _read_or_hash(target, limit=3)


def test_registered_worktree_accepts_git_path_spelling(tmp_path: Path, monkeypatch) -> None:
    from openprogram.programs.tools.system import self_update

    candidate = tmp_path / "candidate"
    sha = "a" * 40
    monkeypatch.setattr(
        self_update, "_git",
        lambda *args: f"worktree {candidate.as_posix()}\0HEAD {sha}\0branch refs/heads/fix\0\0",
    )
    self_update._validate_registered_worktree(tmp_path, candidate, sha, "fix")
    with pytest.raises(self_update.SelfUpdateToolError):
        self_update._validate_registered_worktree(tmp_path, candidate, "b" * 40, "fix")


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
