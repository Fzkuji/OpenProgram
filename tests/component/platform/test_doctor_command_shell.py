"""``openprogram doctor`` reports the shell commands run in, and runs one."""
import sys

import pytest

from openprogram.host._platform import command_shell
from openprogram.host._platform.command_shell import ShellProfile
from openprogram.host.backend import base
from openprogram.operations.diagnostics import checks


@pytest.fixture
def unsandboxed(monkeypatch):
    monkeypatch.setattr("openprogram.host.sandbox.resolve_policy", lambda **kw: None)


def _row():
    ok, label, detail = checks._check_command_shell()
    return {"ok": ok, "label": label, "detail": detail}


def test_doctor_lists_the_command_shell_row(monkeypatch):
    monkeypatch.setattr(checks, "CHECKS", ())
    monkeypatch.setattr(checks, "runtime_http_checks", lambda: [])
    monkeypatch.setattr(checks, "_check_command_shell", lambda: (True, "command shell", "x"))
    monkeypatch.setattr("openprogram.host.system_access.doctor_rows", lambda: [])
    rows = checks.run_checks()
    assert {"id": "command_shell", "ok": True, "label": "command shell", "detail": "x",
            "optional": True, "status": "granted"} in rows


@pytest.mark.parametrize("check", [
    lambda: (False, "command shell", "round trip failed: exit 1"),
    lambda: (_ for _ in ()).throw(RuntimeError("sandbox cannot run")),
])
def test_a_failed_shell_row_is_reported_but_gates_nothing(monkeypatch, check):
    # Upgrade's cold-start probe and the self-update probe need every row ok;
    # the shell row is advice, like the system access rows, so a sandbox that
    # cannot run or a slow first Git Bash start never refuses an upgrade.
    monkeypatch.setattr(checks, "CHECKS", ())
    monkeypatch.setattr(checks, "runtime_http_checks", lambda: [])
    monkeypatch.setattr(checks, "_check_command_shell", check)
    monkeypatch.setattr("openprogram.host.system_access.doctor_rows", lambda: [])
    row = next(r for r in checks.run_checks() if r["id"] == "command_shell")
    assert (row["ok"], row["optional"], row["status"]) == (True, True, "failed")
    assert row["detail"]


@pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX host shell")
def test_doctor_runs_one_command_through_the_shell(unsandboxed):
    row = _row()
    assert row["ok"] is True, row
    profile = command_shell.host_shell()
    assert row["label"] == "command shell"
    assert f"{profile.name} ({profile.path})" in row["detail"]
    assert "not sandboxed" in row["detail"]
    assert "recoverable_delete=supported" in row["detail"]
    assert "round trip: UTF-8 text and exit code 3 came back" in row["detail"]


def test_a_shell_that_loses_the_exit_code_or_the_text_fails(monkeypatch, unsandboxed):
    seen = []

    def run(self, command, timeout, cwd=None):
        seen.append(command)
        return base.RunResult(1, "??", "")

    monkeypatch.setattr("openprogram.host.backend.local.LocalBackend.run", run)
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: ShellProfile(
        "powershell51", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "system",
        edition="5.1"))
    row = _row()
    assert row["ok"] is False
    assert seen == ["Write-Output '你好'; exit 3"]
    assert "PowerShell 5.1" in row["detail"]
    assert "recoverable_delete=unsupported" in row["detail"]
    assert "utf8_file_output=partial" in row["detail"]
    assert "round trip failed: exit 1" in row["detail"]


def test_a_named_bash_that_is_not_a_file_is_reported(monkeypatch, unsandboxed):
    monkeypatch.setattr("openprogram.host.backend.local.LocalBackend.run",
                        lambda self, command, timeout, cwd=None: base.RunResult(3, "你好", ""))
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: ShellProfile(
        "git-bash", r"C:\Program Files\Git\bin\bash.exe", "install", override_unused=r"D:\nope\bash.exe"))
    row = _row()
    assert row["ok"] is True
    assert r"the named bash D:\nope\bash.exe is not a file and is not used" in row["detail"]


def test_a_sandbox_that_cannot_run_is_named_with_what_it_needs(monkeypatch):
    from openprogram.host import sandbox
    monkeypatch.setattr("openprogram.host.backend.local.LocalBackend.run",
                        lambda self, command, timeout, cwd=None: base.RunResult(3, "你好", ""))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda **kw: object())
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "Sandbox unavailable: needs WSL2 (x)")
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: ShellProfile(
        "powershell51", "powershell.exe", "system", edition="5.1"))
    row = _row()
    assert "not sandboxed" in row["detail"]
    assert "the sandbox is on but cannot run here: Sandbox unavailable: needs WSL2" in row["detail"]
