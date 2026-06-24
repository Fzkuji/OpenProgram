"""Tests for openprogram.sandbox — system-level sandbox."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

from openprogram.sandbox import (
    _bwrap_args,
    _seatbelt_profile,
    is_available,
    sandbox_enabled,
    wrap_command,
)


def test_seatbelt_profile_contains_cwd():
    profile = _seatbelt_profile("/my/project")
    assert '(allow file-write* (subpath "/my/project"))' in profile
    assert "(version 1)" in profile
    assert "(deny default)" in profile


def test_bwrap_args_structure():
    args = _bwrap_args("echo hello", "/my/project")
    assert args[0] == "bwrap"
    assert "--ro-bind" in args
    assert "--bind" in args
    idx = args.index("--bind")
    assert args[idx + 1] == "/my/project"
    assert args[idx + 2] == "/my/project"
    assert args[-3:] == ["bash", "-c", "echo hello"]


def test_wrap_command_returns_list():
    args, shell = wrap_command("ls", "/tmp/test")
    assert isinstance(args, list)
    assert shell is False
    assert any("bash" in a for a in args)
    assert "-c" in args
    assert "ls" in args


def test_sandbox_enabled_default_false():
    assert sandbox_enabled.get(False) is False


def test_sandbox_enabled_toggle():
    token = sandbox_enabled.set(True)
    try:
        assert sandbox_enabled.get(False) is True
    finally:
        sandbox_enabled.reset(token)
    assert sandbox_enabled.get(False) is False


def test_is_available_returns_bool():
    result = is_available()
    assert isinstance(result, bool)


@pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"),
    reason="macOS with sandbox-exec required",
)
class TestSeatbeltEndToEnd:
    def test_write_inside_cwd_allowed(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            args, _ = wrap_command(f"echo ok > {td}/allowed.txt", td)
            result = subprocess.run(args, capture_output=True, text=True, timeout=5)
            assert result.returncode == 0
            assert os.path.exists(os.path.join(td, "allowed.txt"))

    def test_write_outside_cwd_denied(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            target = os.path.expanduser("~/test_sandbox_should_fail.txt")
            args, _ = wrap_command(f"echo bad > {target}", td)
            result = subprocess.run(args, capture_output=True, text=True, timeout=5)
            assert result.returncode != 0
            assert not os.path.exists(target)


def test_invocation_sandbox_integration():
    """_invocation respects sandbox_enabled contextvar."""
    from openprogram.backend.local import _invocation

    token = sandbox_enabled.set(True)
    try:
        if is_available():
            args, shell = _invocation("echo hi", cwd="/tmp")
            assert isinstance(args, list)
            assert shell is False
        else:
            args, shell = _invocation("echo hi", cwd="/tmp")
            if sys.platform != "win32":
                assert shell is True
    finally:
        sandbox_enabled.reset(token)
