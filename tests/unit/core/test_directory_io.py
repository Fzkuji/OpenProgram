"""Exercise path-based directory IO even on hosts with native dir_fd support."""
import os
from types import SimpleNamespace

import pytest

from openprogram.host._platform import filesystem


def test_path_directory_backend_reads_exact_binary_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "_DIRECTORY_FD_SUPPORTED", False)
    root = tmp_path / "folder with spaces"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    payload = b"line\r\nnext\n\x1a\x00\xff"
    (nested / "data.bin").write_bytes(payload)
    handle = filesystem.directory_handle(root)
    child = filesystem.directory_child(filesystem.directory_duplicate(handle), "nested")
    fd = filesystem.directory_read_file(child, "data.bin")
    try:
        assert os.read(fd, 1000) == payload
    finally:
        os.close(fd)
        filesystem.directory_close(child)
        filesystem.directory_close(handle)


def test_user_state_read_is_bounded_and_binary(tmp_path):
    target = tmp_path / "state"
    target.write_bytes(b"a\r\n\x1a")
    filesystem.restrict_to_user(target)
    assert filesystem.read_user_state_bytes(target, limit=4) == b"a\r\n\x1a"
    with pytest.raises(ValueError, match="invalid|limit"):
        filesystem.read_user_state_bytes(target, limit=3)


def test_path_backend_detects_file_replacement_during_open(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "_DIRECTORY_FD_SUPPORTED", False)
    target = tmp_path / "state"
    target.write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"changed")
    original_open = os.open

    def raced_open(path, *args, **kwargs):
        if os.fspath(path) == str(target):
            os.replace(replacement, target)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", raced_open)
    with pytest.raises(OSError, match="changed while opening"):
        filesystem.directory_read_file(filesystem.directory_handle(tmp_path), "state")


def test_checkpoint_rejects_junction_metadata_without_needing_link_privileges(tmp_path, monkeypatch):
    from openprogram.state.store.snapshot.checkpoint import CheckpointStore
    original_lstat = os.lstat

    def junction_lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if os.fspath(path) == str(tmp_path):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(os, "lstat", junction_lstat)
    with pytest.raises(OSError, match="unsafe parent"):
        CheckpointStore._capture_parent_chain(str(tmp_path / "file.txt"))
