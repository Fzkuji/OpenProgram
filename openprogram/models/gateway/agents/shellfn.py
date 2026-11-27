"""A shell function OpenProgram puts in the user's shell profiles for an agent.

After yetone/magpie ``internal/update/shellfn.go``, ``shellfn_other.go``,
``shellfn_windows.go`` and ``cli_path.go`` ``cliProfile`` (MIT License,
Copyright (c) yetone). agy takes the gateway, its key and the model only
from how it is started (``agy.py``), so OpenProgram puts a function named
``agy`` in the profile of each shell the user has, which starts it through
OpenProgram (no command to copy at each start). The function sits in a block
of its own between two marked lines, which is all OpenProgram ever writes or
takes out of the profile; the rest of the file is the user's.

The shells are zsh (its ``.zshrc`` in ``ZDOTDIR`` when that is set), bash
(the file a terminal's bash reads) and fish (``config.fish``) on macOS and
Linux, PowerShell 7 and Windows PowerShell on Windows, each only where its
execution policy runs a profile. A profile that isn't there yet is made only
for the shell a terminal opens, and only a profile inside the home is
written, so a test's temporary home keeps it from the real one.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_lock = threading.Lock()


@dataclass(frozen=True)
class Profile:
    """One shell's profile a function goes in."""

    kind: str          # posix, fish, powershell
    path: Path
    create: bool       # made when it isn't there: the shell a terminal opens


def _open(name: str) -> str:
    return f"# >>> openprogram {name} >>>"


def _close(name: str) -> str:
    return f"# <<< openprogram {name} <<<"


def _home() -> str:
    return os.environ.get("USERPROFILE" if sys.platform == "win32" else "HOME", "")


def _find_shell(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _installed_shells() -> list[tuple[str, bool]]:
    """The shells installed here, ``(name, is the one a terminal opens)`` (magpie ``installedShells``)."""
    login = os.environ.get("SHELL", "")
    out = [(name, os.path.basename(login) == name) for name in ("zsh", "bash", "fish") if _find_shell(name)]
    if login and os.path.isabs(login) and not any(default for _, default in out):
        out.insert(0, (os.path.basename(login), True))
    return out


def _cli_profile(shell: str) -> str:
    """The profile of ``shell`` (magpie ``cliProfile``), "" for none."""
    home = os.environ.get("HOME", "")
    if not home:
        return ""
    if shell == "zsh":
        zdot = os.environ.get("ZDOTDIR", "")
        return os.path.join(zdot if os.path.isabs(zdot) else home, ".zshrc")
    if shell == "bash":
        # Terminal on the Mac opens a login bash; a Linux terminal an interactive one
        return os.path.join(home, ".bash_profile" if sys.platform == "darwin" else ".bashrc")
    if shell == "fish":
        config = os.environ.get("XDG_CONFIG_HOME", "")
        return os.path.join(config if os.path.isabs(config) else os.path.join(home, ".config"), "fish", "config.fish")
    return ""


def _posix_profiles() -> list[Profile]:
    """zsh's, bash's and fish's profiles, the one a terminal opens made when
    it isn't there yet. A Mac's login bash reads the first of
    ``.bash_profile``, ``.bash_login`` and ``.profile`` there is."""
    out = []
    for name, default in _installed_shells():
        path = _cli_profile(name)
        if not path:
            continue
        kind, create = "posix", default
        if name == "fish":
            kind = "fish"
            # fish makes ~/.config/fish on its first start, and reads config.fish there when there is one
            if os.path.isdir(os.path.dirname(path)):
                create = True
        elif name == "bash" and sys.platform == "darwin" and not os.path.lexists(path):
            for other in (".bash_login", ".profile"):
                candidate = os.path.join(os.path.dirname(path), other)
                if os.path.lexists(candidate):
                    path = candidate
                    break
        out.append(Profile(kind, Path(path), create))
    return out


def _windows_policy(root: int, key: str) -> str:
    try:
        import winreg  # type: ignore[import-not-found]
        with winreg.OpenKey(root, key) as handle:
            if key == r"SOFTWARE\Policies\Microsoft\Windows\PowerShell":
                try:
                    if winreg.QueryValueEx(handle, "EnableScripts")[0] == 0:
                        return "Restricted"
                except OSError:
                    pass
            return str(winreg.QueryValueEx(handle, "ExecutionPolicy")[0])
    except (ImportError, OSError):
        return ""


def _runs_profile(policy: str) -> bool:
    return policy.lower() in ("remotesigned", "unrestricted", "bypass")


def _windows_profiles() -> list[Profile]:
    """PowerShell 7's and Windows PowerShell's profiles, each only where its
    execution policy runs a profile: Windows PowerShell's is Restricted
    until the user changes it, and a profile it may not run would put an
    error in each terminal it opens. cmd has no profile."""
    home = os.environ.get("USERPROFILE", "")
    if not home:
        return []
    docs = Path(home) / "Documents"
    try:
        import winreg  # type: ignore[import-not-found]
        machine, user = winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER
    except ImportError:
        return []
    gpo = _windows_policy(machine, r"SOFTWARE\Policies\Microsoft\Windows\PowerShell")
    out = []
    seven = docs / "PowerShell"
    if seven.exists() or shutil.which("pwsh"):
        # RemoteSigned unless a policy says otherwise
        if not gpo or _runs_profile(gpo):
            out.append(Profile("powershell", seven / "Microsoft.PowerShell_profile.ps1", True))
    policy = gpo or _windows_policy(user, r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") \
        or _windows_policy(machine, r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell")
    if _runs_profile(policy):
        out.append(Profile("powershell", docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1", True))
    return out


def shell_profiles() -> list[Profile]:
    """This machine's shells' profiles (a test sets it)."""
    return _windows_profiles() if sys.platform == "win32" else _posix_profiles()


def _in_home(path: Path) -> bool:
    home = _home()
    if not home or not os.path.isabs(home) or not path.is_absolute():
        return False
    try:
        rel = os.path.relpath(path, home)
    except ValueError:
        return False
    return rel != "." and not rel.startswith("..")


def home_profiles() -> list[Profile]:
    """This machine's shells' profiles inside the user's home."""
    return [p for p in shell_profiles() if _in_home(p.path)]


def _find(lines: list[str], name: str) -> int:
    for i, line in enumerate(lines):
        if line.rstrip("\r") == _open(name):
            return i
    return -1


def with_fn_block(text: str, name: str, body: str, crlf: bool = False) -> str:
    """The profile text with the function ``name``'s block as ``body``, in
    place of the one it has or after the rest; ``body`` "" takes the block
    out, and the blank line put before it. The file's own line endings are
    kept; a new file gets CRLF for PowerShell."""
    nl = "\r\n" if ("\r\n" in text or (text == "" and crlf)) else "\n"
    block = [_open(name), *body.replace("\r\n", "\n").split("\n"), _close(name)] if body else []
    lines = text.replace("\r\n", "\n").split("\n")
    i = _find(lines, name)
    if i >= 0:
        end = next((j for j in range(i + 1, len(lines)) if lines[j] == _close(name)), -1)
        if end < 0:
            # opened and never closed: someone cut it; the open line alone is OpenProgram's
            end = i
        if body:
            return nl.join(lines[:i] + block + lines[end + 1:])
        start = i - 1 if i > 0 and lines[i - 1] == "" else i
        return nl.join(lines[:start] + lines[end + 1:])
    if not body:
        return text
    out = text
    if text:
        if not text.endswith("\n"):
            out += nl
        out += nl
    return out + nl.join(block) + nl


def _set_block(profile: Profile, name: str, body: str) -> bool:
    """Write the function's block in ``profile`` as ``body`` ("" takes it out);
    whether the block is there after."""
    try:
        raw: Optional[bytes] = profile.path.read_bytes()
    except FileNotFoundError:
        if not body or not profile.create:
            return False
        raw = None
    bom = b"\xef\xbb\xbf"
    text = (raw or b"").removeprefix(bom).decode("utf-8", errors="surrogateescape")
    out = with_fn_block(text, name, body, profile.kind == "powershell")
    if out == text and raw is not None:
        return bool(body)
    data = out.encode("utf-8", errors="surrogateescape")
    if raw is None:
        # Windows PowerShell reads a profile without a byte order mark in the
        # system's code page, which a path of the user's name outside ASCII would be garbled in
        if profile.kind == "powershell":
            data = bom + data
    elif raw.startswith(bom):
        data = bom + data
    # a profile linked into a dotfiles folder stays a link, its file written,
    # and one with other hard links is written in place (magpie edit.WriteAtomic)
    from ..edit import write_bytes_atomic
    write_bytes_atomic(profile.path, data, keep_mode=raw is not None, mode=0o644)
    return bool(body)


def shell_function_at(profiles: list[Profile], name: str, body: Callable[[str], str]) -> list[str]:
    """:func:`shell_function` for the profiles ``profiles`` (magpie
    ``ShellFunctionAt``), ``body`` taking the shell's kind alone."""
    found = []
    with _lock:
        for profile in profiles:
            if not profile.path.is_absolute():
                continue
            try:
                if _set_block(profile, name, body(profile.kind).rstrip("\n")):
                    found.append(str(profile.path))
            except OSError:
                continue
    return found


def drop_shell_function_at(profiles: list[Profile], name: str) -> None:
    """:func:`drop_shell_function` for the profiles ``profiles``."""
    with _lock:
        for profile in profiles:
            if profile.path.is_absolute():
                try:
                    _set_block(profile, name, "")
                except OSError:
                    continue


def shell_function_in_at(profiles: list[Profile], name: str) -> list[str]:
    """:func:`shell_function_in` for the profiles ``profiles``."""
    out = []
    for profile in profiles:
        try:
            text = profile.path.read_bytes().removeprefix(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
        except OSError:
            continue
        if _find(text.replace("\r\n", "\n").split("\n"), name) >= 0:
            out.append(str(profile.path))
    return out


def cli_program() -> list[str]:
    """The command that runs this OpenProgram, by a path that stays put
    (magpie ``cliExe``): its Python with ``-I -m openprogram``, and the profile
    when it isn't the default."""
    # -I: a cwd holding a package or module of the same name (a cloned repo's
    # openprogram/, a json.py) is never imported, as in the App's own launch
    command = [sys.executable, "-I", "-m", "openprogram"]
    try:
        from openprogram.state.paths import get_active_profile
        profile = get_active_profile()
    except Exception:  # noqa: BLE001 - the default profile
        profile = None
    if profile and profile != "default":
        command += ["--profile", profile]
    return command


def shell_function(name: str, body: Callable[[str, list[str]], str]) -> list[str]:
    """Put the function ``name``, as ``body(kind, program)`` gives it for each
    shell, in the profile of every shell the user has, or bring one there up
    to date (magpie ``ShellFunction``); the profiles that have it now. A
    profile that can't be read or written is left as it is."""
    prog = cli_program()
    return shell_function_at(home_profiles(), name, lambda kind: body(kind, prog))


def drop_shell_function(name: str) -> None:
    """Take the function ``name`` out of every profile OpenProgram put it in."""
    drop_shell_function_at(home_profiles(), name)


def shell_function_in(name: str) -> list[str]:
    """The profiles that hold the function ``name``."""
    return shell_function_in_at(home_profiles(), name)


def tilde_home(path: str) -> str:
    """``path`` with the home as ``~``, for a message."""
    home = _home()
    if home and (path == home or path.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + path[len(home.rstrip(os.sep)):]
    return path


# Starting an agent through its function (magpie plugged_launch.go
# launchSpec, cli/agy_cli.go): what agy's and Dim's functions run. A start
# is a dict: ``bin``, its variables ``env``, the flags put before the user's
# arguments with their values ``args``, the model's ``flag`` ("" none), the
# ``plain`` arguments that start it as it is, and the ``model``, "" while
# the agent isn't on OpenProgram's models.

def posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def fish_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def launch_function(kind: str, prog: list[str], sub: str, start: dict) -> str:
    """The function named ``start["bin"]`` in a shell of ``kind`` (magpie
    ``launchSpec.function``): it runs ``prog`` (this OpenProgram) ``sub
    run``, which starts the real command on the gateway, and the command on
    PATH when ``prog`` is gone. PowerShell's asks ``prog sub env`` for the
    variables, the flags' values and the model, since a GUI program it would
    run isn't waited for."""
    bin = start["bin"]
    exe = prog[0]
    if kind == "fish":
        words = " ".join(fish_quote(p) for p in prog)
        return (f"function {bin}\n    if test -x {fish_quote(exe)}\n        {words} {sub} run $argv\n"
                f"    else\n        command {bin} $argv\n    end\nend")
    if kind == "powershell":
        words = " ".join(ps_quote(p) for p in prog)
        names = ", ".join(ps_quote(n) for n in sorted(start.get("env") or {}))
        args = start.get("args") or []
        pre = "".join(f" {args[i]} $start['{args[i].lstrip('-')}']" for i in range(0, len(args) - 1, 2))
        flag = start.get("flag") or ""
        run = f"        & $real{pre} @args\n"
        if flag:
            name = re.escape(flag.lstrip("-"))
            run = (f"        if (@($args | Where-Object {{ \"$_\" -match '^--?{name}(=|$)' }}).Count) "
                   f"{{ & $real{pre} @args }} else {{ & $real{pre} {flag} $start.model @args }}\n")
        plain = ""
        if start.get("plain"):
            alts = "|".join(re.escape(a) for a in start["plain"])
            plain = f" -or @($args | Where-Object {{ \"$_\" -match '^({alts})(=|$)' }}).Count"
        return (f"function {bin} {{\n"
                f"    $real = Get-Command {bin} -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1\n"
                f"    if (-not $real) {{ Write-Error '{bin} is not installed'; return }}\n"
                "    $start = @{}\n"
                f"    if (Test-Path -LiteralPath {ps_quote(exe)}) {{\n"
                f"        foreach ($l in @(& {words} {sub} env)) {{ $k, $v = \"$l\" -split '=', 2; if ($v) {{ $start[$k] = $v }} }}\n"
                "    }\n"
                f"    if (-not $start.model{plain}) {{ & $real @args; return }}\n"
                f"    $names = @({names})\n"
                "    $was = @{}\n"
                "    foreach ($n in $names) { $was[$n] = [Environment]::GetEnvironmentVariable($n, 'Process'); "
                "[Environment]::SetEnvironmentVariable($n, $start[$n], 'Process') }\n"
                "    try {\n" + run +
                "    } finally { foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $was[$n], 'Process') } }\n"
                "}")
    words = " ".join(posix_quote(p) for p in prog)
    # "bin() {", which dash takes too (a ~/.profile that reads ~/.bashrc is
    # read by sh -lc), not bash's "function bin {"; an alias of its name is
    # taken out first on a line of its own, as a shell expands aliases a
    # line at a time
    return (f"unalias {bin} 2>/dev/null || true\n{bin}() {{\n"
            f"  if [ -x {posix_quote(exe)} ]; then {words} {sub} run \"$@\"; else command {bin} \"$@\"; fi\n}}")


def launch_env_lines(start: dict) -> list[str]:
    """``openprogram <agent> env``: the variables, each flag's value
    (``settings=<file>``) and the model, a ``NAME=value`` a line; nothing
    while the agent isn't on OpenProgram's models."""
    if not start.get("model"):
        return []
    lines = [f"{k}={v}" for k, v in (start.get("env") or {}).items()]
    args = start.get("args") or []
    lines += [f"{args[i].lstrip('-')}={args[i + 1]}" for i in range(0, len(args) - 1, 2)]
    return lines + [f"model={start['model']}"]


def run_launch(start: dict, args: list[str]) -> int:
    """``openprogram <agent> run [args…]``: the command on PATH, given
    OpenProgram's variables, flags and model while ``start`` has a model, and
    as it is otherwise or with one of its ``plain`` arguments. Every argument
    is the agent's, ``--help`` too."""
    bin = start["bin"]
    path = shutil.which(bin)
    if not path:
        sys.stderr.write(f"{bin} isn't installed, or isn't on your PATH\n")
        return 1
    env = dict(os.environ)
    flag = start.get("flag") or ""
    flags = (flag, flag[1:]) if flag.startswith("--") else (flag,)
    model = start.get("model") or ""
    if any(arg.split("=", 1)[0] in tuple(start.get("plain") or ()) for arg in args):
        model = ""
    if model:
        for name in start.get("env") or {}:
            for key in [k for k in env if k.upper() == name.upper()]:
                env.pop(key)
        env.update(start.get("env") or {})
        if flag and not any(arg.split("=", 1)[0] in flags for arg in args):
            args = [flag, model, *args]
        args = [*(start.get("args") or []), *args]
    if sys.platform == "win32":
        import subprocess
        return subprocess.call([path, *args], env=env)
    os.execve(path, [bin, *args], env)
    return 0  # not reached
