from __future__ import annotations

import base64
import sys

import pytest

from openprogram.host.backend import local
from openprogram.host._platform import command_shell, terminal
from openprogram.host._platform.command_shell import ShellProfile

GIT_BASH = ShellProfile("git-bash", r"C:\Git\bin\bash.exe", "install")
POWERSHELL_51 = ShellProfile(
    "powershell51", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "path",
    edition="5.1")


def use_host(monkeypatch, profile):
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: profile)


def test_windows_invocation_prefers_git_bash(monkeypatch) -> None:
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kwargs: None)
    use_host(monkeypatch, GIT_BASH)

    args, use_shell, env, sandboxed = local._invocation("printf ok")

    assert args[:2] == [r"C:\Git\bin\bash.exe", "-c"]
    assert "eval" in args[2]
    assert env["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] == "printf ok"
    assert use_shell is False
    assert sandboxed is False


def test_windows_invocation_falls_back_to_powershell(monkeypatch) -> None:
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kwargs: None)
    use_host(monkeypatch, POWERSHELL_51)

    args, use_shell, _env, sandboxed = local._invocation("Write-Output ok")

    assert args[0] == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert args[1:-1] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-OutputFormat", "Text",
        "-EncodedCommand",
    ]
    assert "\nWrite-Output ok\n" in base64.b64decode(args[-1]).decode("utf-16-le")
    assert use_shell is False
    assert sandboxed is False


def _powershell_source(command: str) -> str:
    args, _env = terminal.powershell_invocation("powershell.exe", command, {})
    return base64.b64decode(args[-1]).decode("utf-16-le")


def test_powershell_command_reaches_the_shell_byte_for_byte():
    # Quotes, percent signs, carets and non-ASCII text never meet cmd.exe or
    # CRT argv quoting: the whole source travels as one base64 argument.
    command = 'Write-Output "50% ^ \'引号\' `"x`"" | Out-String\n# trailing comment'
    args, _env = terminal.powershell_invocation("powershell.exe", command, {})
    assert all(ch.isalnum() or ch in "+/=" for ch in args[-1])
    source = base64.b64decode(args[-1]).decode("utf-16-le")
    before, found, after = source.partition(command)
    assert found
    # The command starts and ends on its own lines, so a closing comment or
    # here-string cannot swallow what follows it.
    assert before.endswith("\n") and after.startswith("\n")


def test_powershell_passes_on_the_last_programs_exit_code():
    # -EncodedCommand alone turns every failure into 1; a hook's "exit 2"
    # from a program, or git diff's 1 versus 2, would be lost.
    after = _powershell_source("git diff --exit-code").rpartition("git diff --exit-code")[2]
    assert after == "\nif (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }"


def test_powershell_output_is_utf8_text_without_progress_records():
    before = _powershell_source("Write-Output ok").partition("Write-Output ok")[0]
    for stream in ("$OutputEncoding", "[Console]::OutputEncoding", "[Console]::InputEncoding"):
        assert stream in before
    assert "[Text.UTF8Encoding]::new()" in before
    assert "$ProgressPreference='SilentlyContinue'" in before


def test_windows_powershell_5_1_writes_redirected_files_as_utf8():
    # 5.1's `>` writes UTF-16, which read and grep see as NUL bytes. As Claude
    # Code does, Out-File (and so `>`) writes UTF-8, with a byte order mark.
    before = _powershell_source("echo hi > a.txt").partition("echo hi > a.txt")[0]
    assert "try { $PSDefaultParameterValues['Out-File:Encoding']='utf8' } catch {}" in before
    assert "Set-Content" not in before


def test_powershell_7_keeps_its_own_file_encoding():
    args, _env = terminal.powershell_invocation(r"C:\Program Files\PowerShell\7\pwsh.exe", "echo hi", {})
    assert "Out-File:Encoding" not in base64.b64decode(args[-1]).decode("utf-16-le")


def test_host_shell_invocation_is_the_command_tools_shell(monkeypatch):
    use_host(monkeypatch, ShellProfile("powershell51", "powershell.exe", "path", edition="5.1"))
    args, use_shell, env = local.host_shell_invocation("Write-Output ok")
    assert args[0] == "powershell.exe" and use_shell is False
    assert env["PYTHONUTF8"] == "1"
    use_host(monkeypatch, GIT_BASH)
    args, use_shell, env = local.host_shell_invocation("printf ok")
    assert args[:2] == [r"C:\Git\bin\bash.exe", "-c"] and use_shell is False
    assert env["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] == "printf ok"


@pytest.mark.parametrize(("windows", "msys"), [
    (r"C:\Users\me\.openprogram\trash\s\run", "/c/Users/me/.openprogram/trash/s/run"),
    (r"d:\Data With Spaces\trash", "/d/Data With Spaces/trash"),
    (r"\\server\share\trash", "//server/share/trash"),
    (r"\\?\C:\long\trash", "/c/long/trash"),
    (r"\\?\UNC\server\share\trash", "//server/share/trash"),
    ("/already/posix", "/already/posix"),
])
def test_windows_paths_are_spelled_for_msys_without_cygpath(windows, msys):
    assert command_shell.msys_path(windows) == msys


def test_git_bash_puts_the_delete_shims_first_with_or_without_cygpath(monkeypatch):
    # cygpath spells the shim folder for the bash it sits beside (/c/... for
    # Git Bash and MSYS2, /cygdrive/c/... for Cygwin); a bash with no cygpath
    # (a scoop shim) gets the MSYS spelling.
    use_host(monkeypatch, GIT_BASH)
    env = {"OPENPROGRAM_RECOVERABLE_TRASH": r"C:\Users\O'Brien\trash\run", "PATH": "x"}
    _args, _shell, child = local.host_shell_invocation("rm -rf build", env)
    source = child["OPENPROGRAM_INTERNAL_SHELL_COMMAND"]
    assert source.startswith(
        'PATH="$(cygpath -u "$OPENPROGRAM_RECOVERABLE_TRASH" 2>/dev/null'
        " || printf %s '/c/Users/O'\"'\"'Brien/trash/run')/shims/bin:$PATH\"; export PATH; ")
    assert source.endswith("; rm -rf build")


def test_shell_transport_does_not_mutate_or_override_caller_environment():
    original = {"PATH": "caller-path", "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    _, child = terminal.git_bash_invocation("bash.exe", r"printf 'C:\Users'", original)
    assert child["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] == r"printf 'C:\Users'"
    assert all(child[key] == value for key, value in original.items())
    assert "OPENPROGRAM_INTERNAL_SHELL_COMMAND" not in original
    _, child = terminal.powershell_invocation("powershell.exe", "Write-Output ok", {})
    assert child["PYTHONUTF8"] == "1"
    assert child["PYTHONIOENCODING"] == "utf-8"


def test_local_backend_hides_windows_shell_window(monkeypatch) -> None:
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (["shell.exe", command], False, None, False),
    )
    seen = {}

    class FakeProcess:
        pid = 123
        returncode = 0
        stdout = None
        stderr = None

        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            return "ok\n", ""

    class Owner:
        def popen(self, args, **kwargs):
            seen.update(args=args, **kwargs)
            return FakeProcess()

        def release(self):
            seen["released"] = True

        def terminate(self):
            seen["terminated"] = True
            return True

    monkeypatch.setattr(local, "ProcessTreeOwner", Owner)

    result = local.LocalBackend().run("echo ok", timeout=5)

    assert result.stdout == "ok\n"
    assert 0 < seen["timeout"] <= 5  # Cancellable collection stays within the budget.
    assert seen["released"] is True
    assert "creationflags" not in seen
