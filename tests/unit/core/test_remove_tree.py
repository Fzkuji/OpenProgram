"""The read-only deletion retry must not alter other permission failures."""
import errno
import os
from types import SimpleNamespace

import pytest

from openprogram import _compat


@pytest.mark.parametrize('attributes', [0, 0x401])
def test_remove_tree_preserves_other_permission_failures(tmp_path, monkeypatch, attributes):
    root = tmp_path / 'owned'
    root.mkdir()
    file = root / 'blocked'
    file.write_bytes(b'keep')
    original_unlink = os.unlink
    original_lstat = os.lstat

    def denied_unlink(path, *, dir_fd=None):
        if os.path.basename(path) == file.name:
            raise PermissionError(errno.EACCES, 'denied', path)
        return original_unlink(path, dir_fd=dir_fd)

    def metadata(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if os.path.basename(path) == file.name:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=attributes)
        return info

    monkeypatch.setattr(os, 'unlink', denied_unlink)
    monkeypatch.setattr(os, 'lstat', metadata)
    monkeypatch.setattr(os, 'chmod', lambda *args, **kwargs: pytest.fail('unexpected chmod'))
    with pytest.raises(PermissionError, match='denied'):
        _compat.remove_tree(root)
    _compat.remove_tree(root, ignore_errors=True)
    assert file.read_bytes() == b'keep'
