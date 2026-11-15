"""An explicit workdir on Windows: which spellings name one directory.

Runs through a fake ``os`` (``ntpath`` rules) for the module, so the
drive-relative and root-relative refusals are checked without a Windows host.
"""
from __future__ import annotations

import ntpath
import stat
import types

import pytest

from openprogram.host import sandbox
from openprogram.host.backend import workdir


@pytest.fixture
def windows(monkeypatch):
    fake = types.SimpleNamespace(
        name="nt",
        path=ntpath,
        getcwd=lambda: r"C:\Users\me\proj",
        stat=lambda path: types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755),
    )
    monkeypatch.setattr(workdir, "os", fake)
    monkeypatch.setattr(ntpath, "realpath", lambda path, **_: ntpath.normpath(path))
    monkeypatch.setattr(sandbox, "validate_read_path", lambda path: None)
    monkeypatch.setattr(sandbox, "validate_write_path", lambda path, cwd=None: None)
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)


@pytest.mark.parametrize(("given", "expected"), [
    ("sub", r"C:\Users\me\proj\sub"),
    (r"sub\..\other", r"C:\Users\me\proj\other"),
    ("sub/deeper", r"C:\Users\me\proj\sub\deeper"),
    (r"D:\data", r"D:\data"),
    ("c:/Users/me/proj/sub", r"c:\Users\me\proj\sub"),
    (r"\\server\share\dir", r"\\server\share\dir"),
])
def test_workspace_relative_and_full_paths_are_one_directory(windows, given, expected):
    command, cwd, reported = workdir.prepare_command(
        "dir", given, backend_id="local", worktree=r"C:\Users\me\proj",
    )
    assert (command, cwd, reported) == ("dir", expected, expected)


@pytest.mark.parametrize("given", ["C:", "C:sub", r"\Users\me", "/tmp", "D:data"])
def test_paths_whose_base_is_the_drives_own_state_are_refused(windows, given):
    # C:sub resolves against that drive's current directory and \x against the
    # current drive: neither is anchored to the conversation's folder.
    with pytest.raises(ValueError, match="workspace-relative or a full absolute path"):
        workdir.prepare_command("dir", given, backend_id="local", worktree=r"C:\Users\me\proj")


@pytest.mark.parametrize("given", [r"C:\remote", r"sub\dir"])
def test_remote_backends_take_posix_paths_only(monkeypatch, given):
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)
    with pytest.raises(ValueError, match="POSIX path"):
        workdir.prepare_command("ls", given, backend_id="ssh", worktree="/srv/app")
