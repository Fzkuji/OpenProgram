"""Which shell a local command runs in, and what the model is told about it.

``command_shell.resolve_shell`` decides it once; the invocation, the
environment block, hooks and rule matching read its answer.
"""
from __future__ import annotations

import ntpath
import sys
import types

import pytest

from openprogram.host._platform import command_shell
from openprogram.host._platform.command_shell import ShellProfile
from openprogram.host.backend import local
from openprogram.runtime.context import components

POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
ENVIRON = {"ProgramFiles": r"C:\Program Files", "ProgramW6432": r"C:\Program Files",
           "LOCALAPPDATA": r"C:\Users\me\AppData\Local", "SystemRoot": r"C:\Windows"}


def use_host(monkeypatch, profile: ShellProfile) -> None:
    monkeypatch.setattr(command_shell, "host_shell", lambda platform=None: profile)


def windows(files=(), path=(), extra=None, *, override="", bundled="") -> ShellProfile:
    """Probe a Windows host whose PATH is ``path`` and whose files are ``files``."""
    environ = {**ENVIRON, "PATH": ";".join(path), "PATHEXT": ".COM;.EXE;.BAT;.CMD",
               **(extra or {})}
    present = {f.lower() for f in files}
    return command_shell._probe_windows(environ=environ, isfile=lambda p: p.lower() in present,
                                        override=override, bundled=bundled)


@pytest.fixture
def unsandboxed(monkeypatch):
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kwargs: None)


# -- the host probe ---------------------------------------------------------

def test_windows_prefers_an_installed_git_bash():
    profile = windows(files={GIT_BASH, PWSH}, path=[ntpath.dirname(PWSH)])
    assert (profile.kind, profile.path, profile.source) == ("git-bash", GIT_BASH, "install")


def test_windows_without_git_takes_pwsh_7_before_windows_powershell():
    profile = windows(files={PWSH, POWERSHELL},
                      path=[ntpath.dirname(POWERSHELL), ntpath.dirname(PWSH)])
    assert (profile.kind, profile.edition, profile.path) == ("pwsh7", "7", PWSH)


def test_windows_with_only_windows_powershell_is_edition_5_1():
    profile = windows(files={POWERSHELL}, path=[ntpath.dirname(POWERSHELL)])
    assert (profile.kind, profile.edition, profile.source) == ("powershell51", "5.1", "path")
    assert windows().path == POWERSHELL and windows().source == "system"


@pytest.mark.parametrize("entry", [".", "", "tools", r".\bin"])
def test_windows_never_searches_the_current_directory(entry):
    # Windows searches the current directory before PATH, and a relative PATH
    # entry is read against it: a repository's own bash.exe would otherwise
    # become the shell for every command. Only absolute PATH entries count.
    profile = windows(files={r"bash.exe", r"tools\bash.exe", r".\bash.exe", r".\bin\bash.exe",
                             POWERSHELL},
                      path=[entry, ntpath.dirname(POWERSHELL)])
    assert profile.kind == "powershell51"


@pytest.mark.parametrize(("found", "kind"), [
    # The Worker's logon task starts in the home folder: shells installed
    # under it are found the same as anywhere else on PATH.
    (r"C:\Users\me\scoop\shims\bash.exe", "git-bash"),
    (r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\pwsh.exe", "pwsh7"),
    (r"C:\Users\me\AppData\Local\Microsoft\WinGet\Links\pwsh.exe", "pwsh7"),
    (r"C:\Program Files\PowerShell\7\pwsh.exe", "pwsh7"),
])
def test_windows_takes_a_shell_on_path_under_the_home_folder(found, kind):
    profile = windows(files={found, POWERSHELL},
                      path=[ntpath.dirname(found), ntpath.dirname(POWERSHELL)])
    assert (profile.kind, profile.path, profile.source) == (kind, found, "path")


def test_windows_probe_does_not_use_which(monkeypatch):
    # shutil.which on Windows searches the current directory before PATH.
    def no_which(*args, **kwargs):
        raise AssertionError("the Windows probe called shutil.which")
    monkeypatch.setattr(command_shell.shutil, "which", no_which)
    found = r"C:\Users\me\scoop\shims\bash.exe"
    assert windows(files={found}, path=[ntpath.dirname(found)]).path == found


def test_windows_finds_bash_through_pathext():
    found = r"C:\msys64\usr\bin\bash.EXE"
    assert windows(files={found}, path=[r"C:\msys64\usr\bin"]).kind == "git-bash"


@pytest.mark.parametrize("launcher", [
    r"C:\Windows\System32\bash.exe",
    r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\bash.exe",
])
def test_windows_never_takes_the_wsl_launcher(launcher):
    assert windows(files={launcher}, path=[ntpath.dirname(launcher)]).kind == "powershell51"


def test_windows_skips_the_wsl_launcher_for_a_bash_later_on_path():
    launcher = r"C:\Windows\System32\bash.exe"
    msys = r"C:\msys64\usr\bin\bash.exe"
    profile = windows(files={launcher, msys}, path=[ntpath.dirname(launcher), ntpath.dirname(msys)])
    assert profile.path == msys


def test_windows_takes_a_bash_on_path_elsewhere():
    found = r"C:\msys64\usr\bin\bash.exe"
    profile = windows(files={found}, path=[ntpath.dirname(found)])
    assert (profile.kind, profile.source) == ("git-bash", "path")


# -- where bash is looked for (named path, bundled Git, beside git.exe) -------

BUNDLED = r"C:\Users\me\.openprogram\runtime\bases\b1\git\usr\bin\bash.exe"


def test_a_named_bash_comes_before_the_install_locations():
    named = r"D:\tools\Git\bin\bash.exe"
    profile = windows(files={named, GIT_BASH}, override=named)
    assert (profile.kind, profile.path, profile.source) == ("git-bash", named, "override")


def test_a_named_bash_that_is_not_a_file_is_passed_over_and_reported():
    profile = windows(files={GIT_BASH}, override=r"D:\missing\bash.exe")
    assert (profile.path, profile.source) == (GIT_BASH, "install")
    assert profile.override_unused == r"D:\missing\bash.exe"
    assert profile.as_dict()["override_unused"] == r"D:\missing\bash.exe"
    fallback = windows(files={POWERSHELL}, path=[ntpath.dirname(POWERSHELL)], override="bash.exe")
    assert fallback.kind == "powershell51" and fallback.override_unused == "bash.exe"


def test_the_environment_variable_names_bash_before_the_setting(monkeypatch):
    monkeypatch.setattr("openprogram.state.settings.setting_value", lambda key: r"C:\from\setting.exe")
    assert command_shell.bash_override({"OPENPROGRAM_GIT_BASH_PATH": r'"C:\env\bash.exe"'}) == r"C:\env\bash.exe"
    assert command_shell.bash_override({}) == r"C:\from\setting.exe"


def test_the_bundled_git_bash_comes_after_an_installed_git_and_before_powershell():
    assert windows(files={GIT_BASH, BUNDLED}, bundled=BUNDLED).source == "install"
    profile = windows(files={BUNDLED, POWERSHELL}, path=[ntpath.dirname(POWERSHELL)], bundled=BUNDLED)
    assert (profile.kind, profile.path, profile.source) == ("git-bash", BUNDLED, "bundled")
    # The manifest names a bash the unpacked Git does not have: PowerShell, as before.
    assert windows(files={POWERSHELL}, path=[ntpath.dirname(POWERSHELL)], bundled=BUNDLED).kind == "powershell51"


def test_bash_is_found_beside_a_scoop_git_on_path():
    git = r"D:\scoop\apps\git\current\cmd\git.exe"
    bash = r"D:\scoop\apps\git\current\bin\bash.exe"
    profile = windows(files={git, bash, POWERSHELL}, path=[ntpath.dirname(git), ntpath.dirname(POWERSHELL)])
    assert (profile.path, profile.source) == (bash, "git")


def test_bash_beside_git_also_looks_in_usr_bin():
    git = r"E:\PortableGit\mingw64\bin\git.exe"
    bash = r"E:\PortableGit\usr\bin\bash.exe"
    profile = windows(files={git, bash}, path=[ntpath.dirname(git)])
    assert (profile.path, profile.source) == (bash, "git")


def test_the_shell_probe_is_cached_per_named_bash(monkeypatch):
    monkeypatch.setattr(command_shell, "_HOST_CACHE", {})
    seen = []
    monkeypatch.setattr(command_shell, "bash_override", lambda environ=None: seen[-1] if seen else "")
    monkeypatch.setattr(command_shell, "_probe_windows",
                        lambda override=None, **kw: ShellProfile("git-bash", override or "x", "override"))
    assert command_shell.host_shell("win32").path == "x"
    seen.append(r"C:\b.exe")
    assert command_shell.host_shell("win32").path == r"C:\b.exe"


# -- what the model is told per PowerShell edition ---------------------------

def test_windows_powershell_5_1_is_told_its_missing_operators():
    syntax = ShellProfile("powershell51", POWERSHELL, "system", edition="5.1").syntax
    assert "&&" in syntax and "if ($?)" in syntax and "2>&1" in syntax
    assert "Set-Content -Encoding utf8" in syntax and "-LiteralPath" in syntax


def test_powershell_7_is_not_told_about_5_1_limits():
    syntax = ShellProfile("pwsh7", PWSH, "path", edition="7").syntax
    assert "&&" not in syntax and "PowerShell 7" in syntax and "-LiteralPath" in syntax


def test_posix_prefers_bash_and_falls_back_to_sh():
    bash = command_shell._probe_posix(isfile=lambda p: p == "/usr/bin/bash",
                                      executable=lambda p: True, which=lambda n: None)
    assert (bash.kind, bash.path) == ("bash", "/usr/bin/bash")
    sh = command_shell._probe_posix(isfile=lambda p: False, executable=lambda p: False,
                                    which=lambda n: None)
    assert (sh.kind, sh.path) == ("sh", "/bin/sh")


# -- the resolver -----------------------------------------------------------

def test_sandbox_and_remote_environments_resolve_in_the_same_function(monkeypatch):
    use_host(monkeypatch, ShellProfile("powershell51", POWERSHELL, "path", edition="5.1"))
    host = command_shell.resolve_shell(sandboxed=False, platform="win32")
    assert host.kind == "powershell51"
    sandboxed = command_shell.resolve_shell(sandboxed=True, platform="win32")
    assert (sandboxed.kind, sandboxed.path, sandboxed.sandboxed) == ("wsl-bash", "/bin/bash", True)
    assert sandboxed.host == host
    assert command_shell.resolve_shell(sandboxed=True, platform="linux").kind == "bash"
    for kind in ("ssh", "docker"):
        assert command_shell.resolve_shell(types.SimpleNamespace(kind=kind)).kind == "remote"


def test_the_sandbox_is_read_on_every_call(monkeypatch):
    use_host(monkeypatch, ShellProfile("bash", "/bin/bash", "system"))
    monkeypatch.setattr(local._sandbox, "unavailable_reason", lambda: None)
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kw: None)
    assert command_shell.resolve_shell().sandboxed is False
    monkeypatch.setattr(local._sandbox, "resolve_policy", lambda **kw: local._sandbox.SandboxPolicy())
    assert command_shell.resolve_shell().sandboxed is True
    monkeypatch.setattr(local._sandbox, "unavailable_reason", lambda: "missing")
    assert command_shell.resolve_shell().sandboxed is False


def test_every_shell_states_every_promise():
    for kind in ("git-bash", "pwsh7", "powershell51", "bash", "sh", "wsl-bash", "remote"):
        promises = ShellProfile(kind, "x", "test").promises
        assert tuple(promises) == command_shell.PROMISES
        assert set(promises.values()) <= {"supported", "partial", "unsupported"}
    # `>` writes UTF-8 with a byte order mark; Set-Content keeps its own default.
    assert ShellProfile("powershell51", "x", "t").promises["utf8_file_output"] == "partial"
    assert ShellProfile("pwsh7", "x", "t").promises["recoverable_delete"] == "unsupported"


# -- the invocation reads it -------------------------------------------------

def test_posix_command_runs_in_bash_not_the_system_sh(monkeypatch, unsandboxed) -> None:
    # /bin/sh is dash on Debian and Ubuntu: `[[`, `source` and arrays fail there.
    use_host(monkeypatch, ShellProfile("bash", "/usr/bin/bash", "system"))

    args, use_shell, _env, sandboxed = local._invocation("[[ -d . ]] && source ./env")

    assert args == ["/usr/bin/bash", "-c", "[[ -d . ]] && source ./env"]
    assert use_shell is False
    assert sandboxed is False


def test_posix_host_without_bash_falls_back_to_sh(monkeypatch, unsandboxed) -> None:
    use_host(monkeypatch, ShellProfile("sh", "/bin/sh", "system"))

    args, use_shell, _env, _sandboxed = local._invocation("echo ok")

    assert (args, use_shell) == ("echo ok", True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell behaviour")
def test_local_command_is_not_in_posix_sh_mode(unsandboxed) -> None:
    if command_shell.host_shell().kind != "bash":
        pytest.skip("host has no bash")
    result = local.LocalBackend().run(
        "shopt -qo posix && echo sh-mode || echo bash-mode; arr=(a b); echo ${#arr[@]}",
        timeout=20,
    )
    assert result.stdout.split() == ["bash-mode", "2"], result.stderr


# -- the environment block renders it ------------------------------------------

def test_environment_names_the_shell_commands_run_in_on_windows(
        monkeypatch, tmp_path, unsandboxed) -> None:
    # COMSPEC is cmd.exe on every Windows machine, and no command runs in it.
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    use_host(monkeypatch, ShellProfile("powershell51", POWERSHELL, "path", edition="5.1"))

    block = components.environment_block()

    assert "cmd.exe" not in block
    assert f"Shell: PowerShell ({POWERSHELL}) — write Windows PowerShell 5.1 syntax, not cmd or bash" in block


def test_environment_names_git_bash_with_its_path_advice(monkeypatch, tmp_path, unsandboxed) -> None:
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    use_host(monkeypatch, ShellProfile("git-bash", GIT_BASH, "install"))

    block = components.environment_block()

    assert (f"Shell: Git Bash ({GIT_BASH}) — write bash syntax; "
            "Windows paths need quotes or forward slashes") in block


def test_environment_names_bash_not_the_login_shell(monkeypatch, tmp_path, unsandboxed) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    use_host(monkeypatch, ShellProfile("bash", "/bin/bash", "system"))

    block = components.environment_block()

    assert "zsh" not in block
    assert "Shell: bash (/bin/bash)\n" in block


def test_environment_leaves_a_remote_shell_to_its_own_line() -> None:
    assert components._command_shell_note(types.SimpleNamespace(kind="ssh")) == ""


@pytest.fixture
def sandboxed_windows(monkeypatch, tmp_path):
    """Windows with the sandbox on and WSL2 bubblewrap able to run."""
    from openprogram.host._platform import environment as platform_environment

    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    monkeypatch.setattr(command_shell.sys, "platform", "win32")
    monkeypatch.setattr(
        local._sandbox, "resolve_policy", lambda **kwargs: local._sandbox.SandboxPolicy())
    monkeypatch.setattr(local._sandbox, "unavailable_reason", lambda: None)
    monkeypatch.setattr(
        platform_environment, "windows_wsl_exec_prefix",
        lambda: ["wsl.exe", "--distribution", "Ubuntu", "--exec"])
    monkeypatch.setattr(
        platform_environment, "windows_path_to_wsl",
        lambda path: "/mnt/c/" + str(path).strip("/\\").replace("\\", "/"))


@pytest.mark.parametrize("host", [
    ShellProfile("powershell51", POWERSHELL, "path", edition="5.1"),
    ShellProfile("git-bash", GIT_BASH, "install"),
])
def test_environment_names_wsl_bash_for_a_sandboxed_windows_command(
        monkeypatch, sandboxed_windows, host) -> None:
    # The prompt must name the shell the invocation really uses.
    use_host(monkeypatch, host)

    args, _shell, _env, sandboxed = local._invocation("echo hi")
    block = components.environment_block()
    shell_line = next(line for line in block.splitlines() if "Shell:" in line)

    assert sandboxed is True
    assert args[0] == "wsl.exe" and args[-3:] == ["/bin/bash", "-c", "echo hi"]
    assert "Shell: bash in WSL (/bin/bash)" in shell_line
    assert "Linux paths" in shell_line
    # The host shell is named only as where an escalated command runs.
    assert shell_line.index(host.name) > shell_line.index("outside the sandbox")
    assert "write PowerShell, not cmd or bash syntax" not in shell_line.split("outside the sandbox")[0]


def test_environment_names_host_shell_when_the_windows_sandbox_cannot_run(
        monkeypatch, sandboxed_windows) -> None:
    use_host(monkeypatch, ShellProfile("powershell51", POWERSHELL, "path", edition="5.1"))
    monkeypatch.setattr(
        local._sandbox, "unavailable_reason", lambda: "Windows sandbox needs wsl.exe")

    block = components.environment_block()

    assert "WSL" not in block
    assert f"Shell: PowerShell ({POWERSHELL})" in block


def test_environment_names_the_sandbox_bash_on_posix(monkeypatch, tmp_path) -> None:
    # A sandboxed command runs /bin/bash even where the host shell is another.
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    monkeypatch.setattr(command_shell.sys, "platform", "linux")
    use_host(monkeypatch, ShellProfile("sh", "/bin/sh", "system"))
    monkeypatch.setattr(
        local._sandbox, "resolve_policy", lambda **kwargs: local._sandbox.SandboxPolicy())
    monkeypatch.setattr(local._sandbox, "unavailable_reason", lambda: None)

    block = components.environment_block()

    assert "Shell: bash (/bin/bash)" in block
    assert "WSL" not in block


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell behaviour")
def test_echo_keeps_backslash_escapes_as_a_sandboxed_command_does(unsandboxed) -> None:
    # macOS /bin/sh expands them in echo; bash, which the sandbox also runs, does not.
    if command_shell.host_shell().kind != "bash":
        pytest.skip("host has no bash")
    result = local.LocalBackend().run(r'echo "a\nb"; printf "c\nd\n"', timeout=20)

    assert result.stdout.splitlines() == [r"a\nb", "c", "d"], result.stderr


def test_bash_tool_tells_the_model_to_use_printf_for_escapes() -> None:
    from openprogram.programs.tools.files.bash.prompt import DESCRIPTION

    assert r"echo prints backslash escapes such as \n as written; use printf" in DESCRIPTION


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("reason", [None, "bwrap missing"])
@pytest.mark.parametrize("passed", [False, True])
def test_the_invocation_runs_where_resolve_shell_says(monkeypatch, configured, reason, passed):
    # One owner: a caller that passes its own policy (cron, MCP and LSP
    # clients) runs where resolve_shell reports for that policy.
    use_host(monkeypatch, ShellProfile("bash", "/bin/bash", "system"))
    policy = local._sandbox.SandboxPolicy()
    monkeypatch.setattr(local._sandbox, "resolve_policy",
                        lambda **kw: policy if configured else None)
    monkeypatch.setattr(local._sandbox, "unavailable_reason", lambda: reason)
    monkeypatch.setattr(local._sandbox, "unavailable_policy", lambda: "warn")
    monkeypatch.setattr(local._sandbox, "wrap_command",
                        lambda command, cwd, policy: (["sandboxed", command], False))
    given = policy if passed else None

    reported = command_shell.resolve_shell(policy=given, platform="linux")
    _args, _shell, _env, sandboxed = local._invocation("echo hi", policy=given)

    assert sandboxed is reported.sandboxed
    assert sandboxed is ((configured or passed) and reason is None)


# -- Windows without WSL2: the sandbox switch does not make every command fail

def test_windows_without_wsl_runs_the_command_in_the_host_shell(monkeypatch):
    use_host(monkeypatch, ShellProfile("powershell51", POWERSHELL, "system", edition="5.1"))
    sandbox = local._sandbox
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    monkeypatch.setattr(sandbox, "_config_section", lambda: {})
    monkeypatch.setattr(sandbox, "resolve_policy", lambda **kw: sandbox.SandboxPolicy())
    monkeypatch.setattr(sandbox.platform_environment, "windows_wsl_sandbox_reason",
                        lambda: "Windows sandbox needs wsl.exe")
    assert sandbox.unavailable_policy() == "warn"
    reason = sandbox.unavailable_reason()
    assert reason.startswith("Sandbox unavailable: needs WSL2") and "wsl --install" in reason
    args, _shell, _env, sandboxed = local._invocation("Write-Output ok")
    assert sandboxed is False and args[0] == POWERSHELL


def test_refuse_stays_the_default_off_windows(monkeypatch):
    sandbox = local._sandbox
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "_config_section", lambda: {})
    assert sandbox.unavailable_policy() == "refuse"
    monkeypatch.setattr(sandbox, "_config_section", lambda: {"unavailable_policy": "refuse"})
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    assert sandbox.unavailable_policy() == "refuse"
