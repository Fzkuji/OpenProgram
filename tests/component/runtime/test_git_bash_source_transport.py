"""Real MSYS shell source keeps Python/JSON escapes across Windows argv."""
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from openprogram.backend import local


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native MSYS command-line parsing")


@pytest.fixture
def native_bash(monkeypatch):
    if not local._windows_bash():
        pytest.skip("Git Bash not installed")
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kw: None)


@pytest.mark.parametrize("value", [
    r"C:\Users\project", "ends\\", "\\\\server\\share", 'quote" and apostrophe\'',
    "中文 空格 $HOME `literal`", "first\nsecond\tthird",
])
def test_git_bash_keeps_nested_source_literals(value, native_bash):
    code = "print(repr(" + repr(value) + "))"
    command = shlex.join([Path(sys.executable).as_posix(), "-c", code])
    result = local.LocalBackend().run(command, timeout=10)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == repr(value)


def test_command_transport_is_not_exported_to_descendants(native_bash):
    code = "import os; print(os.environ.get('OPENPROGRAM_INTERNAL_SHELL_COMMAND', 'absent')); print(os.environ.get('__openprogram_source', 'absent'))"
    result = local.LocalBackend().run(shlex.join([Path(sys.executable).as_posix(), "-c", code]), timeout=10)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == ["absent", "absent"]
    assert "OPENPROGRAM_INTERNAL_SHELL_COMMAND" not in os.environ


def test_multiline_shell_and_exit_status_are_preserved(native_bash):
    result = local.LocalBackend().run("cat <<'END'\nC:\\Users\\project\nEND\nexit 7", timeout=10)
    assert result.exit_code == 7
    assert result.stdout.strip() == r"C:\Users\project"


def test_native_powershell_fallback_preserves_unicode_and_paths(monkeypatch):
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kw: None)
    monkeypatch.setattr(local, "_windows_bash", lambda: None)
    shell = shutil.which("powershell.exe")
    assert shell
    monkeypatch.setattr(local, "_windows_powershell", lambda: shell)
    result = local.LocalBackend().run("Write-Output '中文 C:\\Users\\project'; exit 7", timeout=10)
    assert result.exit_code == 7
    assert result.stdout.strip() == r"中文 C:\Users\project"
