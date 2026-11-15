"""The recoverable-delete shim prefix, run in a real bash.

These start a ``bash`` process, so they live in the component tree; the
prefix's text is checked without a process in ``tests/unit/core/test_windows_shell.py``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from openprogram.host.backend import local
from openprogram.host._platform import command_shell
from openprogram.host._platform.command_shell import ShellProfile


def use_host(monkeypatch, profile):
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: profile)


@pytest.mark.skipif(sys.platform == "win32", reason="runs a POSIX bash with a stand-in cygpath")
def test_the_shim_prefix_takes_the_spelling_cygpath_gives(monkeypatch, tmp_path):
    # Under Cygwin the drive is /cygdrive/c, which the MSYS spelling misses:
    # the real rm would run and the delete could not be recovered.
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    cygdrive = tmp_path / "cygdrive" / "c" / "trash"
    (cygdrive / "shims" / "bin").mkdir(parents=True)
    (cygdrive / "shims" / "bin" / "rm").write_text("#!/bin/sh\necho shimmed \"$@\"\n")
    (cygdrive / "shims" / "bin" / "rm").chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "cygpath").write_text(f"#!/bin/sh\necho '{cygdrive}'\n")
    (tools / "cygpath").chmod(0o755)
    use_host(monkeypatch, ShellProfile("git-bash", bash, "path"))
    env = {"OPENPROGRAM_RECOVERABLE_TRASH": r"C:\trash", "PATH": f"{tools}:/usr/bin:/bin"}
    args, _shell, child = local.host_shell_invocation("rm -rf build", env)
    done = subprocess.run(args, env=child, capture_output=True, text=True, timeout=20)
    assert done.stdout.strip() == "shimmed -rf build", done.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="runs a POSIX bash with no cygpath")
def test_the_shim_prefix_runs_in_a_bash_without_cygpath(monkeypatch, tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    shims = tmp_path / "trash run" / "shims" / "bin"
    shims.mkdir(parents=True)
    (shims / "rm").write_text("#!/bin/sh\necho shimmed \"$@\"\n")
    (shims / "rm").chmod(0o755)
    use_host(monkeypatch, ShellProfile("git-bash", bash, "path"))
    env = {"OPENPROGRAM_RECOVERABLE_TRASH": str(tmp_path / "trash run"),
           "PATH": "/usr/bin:/bin"}
    args, _shell, child = local.host_shell_invocation("rm -rf build", env)
    done = subprocess.run(args, env=child, capture_output=True, text=True, timeout=20)
    assert done.stdout.strip() == "shimmed -rf build", done.stderr
