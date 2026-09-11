"""macOS bookmark and directory-event probes against temporary folders."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from openprogram.store.project import native

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS native probes")


def test_bookmark_follows_renamed_temporary_folder(tmp_path: Path):
    folder = tmp_path / "paper"
    folder.mkdir()
    blob = native.create_bookmark(folder)
    assert blob
    moved = tmp_path / "research"
    folder.rename(moved)
    resolved = native.resolve_bookmark(blob)
    assert resolved is not None
    assert Path(resolved).resolve() == moved.resolve()


def test_bookmark_does_not_adopt_a_copy(tmp_path: Path):
    import shutil
    folder = tmp_path / "paper"
    folder.mkdir()
    blob = native.create_bookmark(folder)
    copy = tmp_path / "copy"
    shutil.copytree(folder, copy)
    resolved = native.resolve_bookmark(blob)
    assert Path(resolved).resolve() == folder.resolve()
    assert Path(resolved).resolve() != copy.resolve()


def test_directory_observer_sees_rename_and_stops(tmp_path: Path):
    folder = tmp_path / "paper"
    folder.mkdir()
    seen = []
    ready = threading.Event()

    def on_paths(changed):
        seen.extend(changed)
        ready.set()

    observer = native.NativePathObserver(on_paths)
    observer.start([folder, folder.parent])
    try:
        deadline = time.monotonic() + 2
        while observer._stream is None and time.monotonic() < deadline:
            time.sleep(0.05)
        moved = tmp_path / "moved"
        folder.rename(moved)
        assert ready.wait(3)
        assert seen
    finally:
        observer.stop()
    assert observer._stream is None
    assert observer._thread is None or not observer._thread.is_alive()
