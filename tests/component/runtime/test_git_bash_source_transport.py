"""Real MSYS shell source keeps Python/JSON escapes across Windows argv."""
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from openprogram.host.backend import local
from openprogram.host._platform import command_shell


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native MSYS command-line parsing")


@pytest.fixture
def native_bash(monkeypatch):
    if command_shell.host_shell().kind != "git-bash":
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
    shell = shutil.which("powershell.exe")
    assert shell
    monkeypatch.setattr(command_shell, "host_shell",
                        lambda platform=None: command_shell.ShellProfile("powershell51", shell, "path"))
    result = local.LocalBackend().run("Write-Output '中文 C:\\Users\\project'; exit 7", timeout=10)
    assert result.exit_code == 7
    assert result.stdout.strip() == r"中文 C:\Users\project"


@pytest.mark.parametrize("command", ["exit 3", "cmd /c exit 3"])
def test_powershell_passes_the_exit_code_in_constrained_language_mode(command):
    # Under AppLocker or WDAC, PowerShell runs a command in ConstrainedLanguage,
    # where the UTF-8 setup before it cannot touch .NET types. That setup is
    # contained, and `exit` (not $host.SetShouldExit) works in either mode.
    import base64
    import subprocess

    from openprogram.host._platform import terminal

    shell = shutil.which("powershell.exe")
    assert shell
    args, _env = terminal.powershell_invocation(shell, command, {})
    source = base64.b64decode(args[-1]).decode("utf-16-le")
    done = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'; "
         "Invoke-Expression $env:OPENPROGRAM_TEST_SOURCE"],
        env={**os.environ, "OPENPROGRAM_TEST_SOURCE": source},
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 3, (done.stdout, done.stderr)
