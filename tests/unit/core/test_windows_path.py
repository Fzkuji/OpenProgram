from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprogram import _compat
from openprogram.store.session.git_session import atomic_write_text


def test_filesystem_path_preserves_short_windows_paths(tmp_path: Path) -> None:
    assert _compat.filesystem_path(tmp_path) == str(tmp_path.absolute())


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
