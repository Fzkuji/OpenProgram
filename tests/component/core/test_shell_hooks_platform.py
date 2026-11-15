"""A user's hook: which shell runs it, and in which encoding it is spoken to."""
from __future__ import annotations

import base64
import subprocess
import sys
import types

import pytest

from openprogram.host._platform import command_shell
from openprogram.host._platform.command_shell import ShellProfile
from openprogram.runtime.events import shell_hooks


def _event(text: str = "写入 /tmp/x"):
    return types.SimpleNamespace(
        id="e1", ts=1.0, type="tool.before", origin="test",
        payload={"note": text}, metadata={},
    )


@pytest.fixture
def launched(monkeypatch):
    calls: list[dict] = []

    def run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="不允许\n")

    monkeypatch.setattr(shell_hooks.subprocess, "run", run)
    return calls


def test_windows_hook_runs_in_powershell_without_git_bash(monkeypatch, launched):
    monkeypatch.setattr(shell_hooks.sys, "platform", "win32")
    monkeypatch.setattr(command_shell, "host_shell",
                        lambda platform=None: ShellProfile("powershell51", "powershell.exe", "path"))
    monkeypatch.setattr(
        "openprogram.host._platform.process_ownership.no_window_creation_flags", lambda: 0x08000000,
    )

    reason = shell_hooks.make_shell_gate("python check.py; exit 2")(_event())

    call = launched[0]
    assert call["args"][0] == "powershell.exe" and call["shell"] is False
    source = base64.b64decode(call["args"][-1]).decode("utf-16-le")
    assert "\npython check.py; exit 2\n" in source
    assert "exit $LASTEXITCODE" in source
    assert call["creationflags"] == 0x08000000
    assert call["encoding"] == "utf-8"
    assert reason == "不允许"


def test_windows_hook_runs_in_git_bash_when_installed(monkeypatch, launched):
    monkeypatch.setattr(shell_hooks.sys, "platform", "win32")
    monkeypatch.setattr(command_shell, "host_shell",
                        lambda platform=None: ShellProfile("git-bash", r"C:\Git\bin\bash.exe", "install"))

    shell_hooks.make_shell_gate("./check.sh || exit 2")(_event())

    call = launched[0]
    assert call["args"][:2] == [r"C:\Git\bin\bash.exe", "-c"] and call["shell"] is False
    assert call["env"]["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] == "./check.sh || exit 2"


def test_posix_hook_keeps_the_system_shell_and_speaks_utf8(monkeypatch, launched):
    monkeypatch.setattr(shell_hooks.sys, "platform", "darwin")

    shell_hooks.make_shell_gate("cat >&2; exit 2")(_event())

    call = launched[0]
    assert call["args"] == "cat >&2; exit 2" and call["shell"] is True
    assert call["env"] is None and call["creationflags"] == 0
    assert (call["encoding"], call["errors"]) == ("utf-8", "replace")
    assert "写入" in call["input"]


@pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX system shell")
def test_gate_denies_with_the_hooks_own_words():
    gate = shell_hooks.make_shell_gate("cat >&2; exit 2")
    assert "写入 /tmp/x" in gate(_event())
    assert shell_hooks.make_shell_gate("exit 0")(_event()) is None
    assert shell_hooks.make_shell_gate("exit 3")(_event()) is None


# -- a hook says which shell it is written for -------------------------------

GIT_BASH = ShellProfile("git-bash", r"C:\Git\bin\bash.exe", "install")
WINDOWS_POWERSHELL = ShellProfile("powershell51", "powershell.exe", "path", edition="5.1")


@pytest.fixture
def windows_host(monkeypatch):
    monkeypatch.setattr(shell_hooks.sys, "platform", "win32")
    monkeypatch.setattr(
        "openprogram.host._platform.process_ownership.no_window_creation_flags", lambda: 0,
    )
    told = []
    monkeypatch.setattr("openprogram.runtime.events.bus.emit_ws_frame", told.append)
    monkeypatch.setattr(shell_hooks, "_told", set())

    def use(profile):
        monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: profile)
    use.told = told
    return use


def test_a_powershell_hook_runs_in_powershell_beside_git_bash(monkeypatch, launched, windows_host):
    windows_host(GIT_BASH)
    monkeypatch.setattr(command_shell, "windows_powershell", lambda environ=None: WINDOWS_POWERSHELL)
    shell_hooks.make_shell_gate("if (Test-Path .env) { exit 2 }", shell="powershell")(_event())
    assert launched[0]["args"][0] == "powershell.exe"
    assert windows_host.told == []


def test_args_run_the_program_directly_without_a_shell(launched, windows_host):
    windows_host(WINDOWS_POWERSHELL)
    shell_hooks.make_shell_gate("python", args=["check.py", "a b"])(_event())
    call = launched[0]
    assert call["args"] == ["python", "check.py", "a b"] and call["shell"] is False
    assert windows_host.told == []


def test_args_run_directly_on_posix_too(monkeypatch, launched):
    monkeypatch.setattr(shell_hooks.sys, "platform", "darwin")
    shell_hooks.make_shell_gate("/usr/bin/python3", args=["check.py"])(_event())
    assert launched[0]["args"] == ["/usr/bin/python3", "check.py"] and launched[0]["shell"] is False


def test_a_bash_hook_without_bash_runs_in_powershell_and_says_so_once(launched, windows_host):
    windows_host(WINDOWS_POWERSHELL)
    gate = shell_hooks.make_shell_gate("test -f .env && exit 2")
    first = _event()
    first.metadata = {"session": "s1"}
    gate(first)
    gate(first)
    other = _event()
    other.metadata = {"session": "s2"}
    gate(other)
    assert all(call["args"][0] == "powershell.exe" for call in launched)
    told = [frame["data"]["session_id"] for frame in windows_host.told]
    assert told == ["s1", "s2"]
    assert "Install Git for Windows" in windows_host.told[0]["data"]["message"]
    assert '"shell": "powershell"' in windows_host.told[0]["data"]["message"]


def test_a_hook_that_times_out_still_allows(monkeypatch, windows_host):
    windows_host(GIT_BASH)

    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

    monkeypatch.setattr(shell_hooks.subprocess, "run", slow)
    assert shell_hooks.make_shell_gate("sleep 99", 1, shell="powershell")(_event()) is None


def test_config_entries_carry_their_shell_and_args():
    class Bus:
        def __init__(self):
            self.gates = []

        def subscribe_gate(self, event_type, gate):
            self.gates.append(gate)

        def subscribe(self, fn, types=None):
            self.gates.append(fn)

    bus = Bus()
    count = shell_hooks.install_config_hooks(bus, {"tool.before": [
        {"command": "a", "shell": "powershell"},
        {"command": "python", "args": ["x.py"]},
        {"command": "b", "args": "not a list"},
    ]})
    assert count == 2


def test_settings_check_the_shell_and_args_of_a_hook():
    from openprogram.state.settings import _validate_hooks
    assert _validate_hooks({"tool.before": [{"command": "a", "shell": "powershell"}]}) is None
    assert _validate_hooks({"tool.before": [{"command": "a", "args": ["b"]}]}) is None
    assert "shell" in _validate_hooks({"tool.before": [{"command": "a", "shell": "cmd"}]})
    assert "args" in _validate_hooks({"tool.before": [{"command": "a", "args": "b"}]})
