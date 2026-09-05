from __future__ import annotations

from openprogram.backend import local


def test_windows_invocation_prefers_git_bash(monkeypatch) -> None:
    monkeypatch.setattr(local.sys, "platform", "win32")
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kwargs: None)
    monkeypatch.setattr(local, "_windows_bash", lambda: r"C:\Git\bin\bash.exe")

    args, use_shell, _env, sandboxed = local._invocation("printf ok")

    assert args == [r"C:\Git\bin\bash.exe", "-c", "printf ok"]
    assert use_shell is False
    assert sandboxed is False


def test_windows_invocation_falls_back_to_powershell(monkeypatch) -> None:
    monkeypatch.setattr(local.sys, "platform", "win32")
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kwargs: None)
    monkeypatch.setattr(local, "_windows_bash", lambda: None)
    monkeypatch.setattr(
        local,
        "_windows_powershell",
        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )

    args, use_shell, _env, sandboxed = local._invocation("Write-Output ok")

    assert args == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Write-Output ok",
    ]
    assert use_shell is False
    assert sandboxed is False


def test_local_backend_hides_windows_shell_window(monkeypatch) -> None:
    monkeypatch.setattr(local.sys, "platform", "win32")
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
