"""Local backend — subprocess.run in the host shell.

The agent's ``bash`` tool routes every command through here. On POSIX
that's ``shell=True`` (the host ``/bin/sh``), exactly as before. On
Windows ``shell=True`` would invoke ``cmd.exe``, which cannot parse the
bash syntax the agent is steered toward (``&&``, pipes, ``$(...)``,
single-quote escaping, heredocs) or run unix coreutils (``rm``/``ls``/
``grep``/…). So on Windows we run the command through a real POSIX bash
(Git Bash / WSL) when one is present, falling back to ``cmd.exe`` only
if no bash exists.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from openprogram.backend.base import Backend, RunResult, decode_maybe


_WIN_BASH_CACHE: str | None | bool = False  # False = not yet probed


def _windows_bash() -> str | None:
    """Path to a POSIX bash on Windows, or None. Prefers Git Bash, which
    handles Windows cwd/paths natively; deliberately skips the WSL
    launcher (``C:\\Windows\\System32\\bash.exe``) because it runs in the
    Linux subsystem with a different filesystem, so a Windows ``cwd``
    wouldn't map. Cached for the process lifetime."""
    global _WIN_BASH_CACHE
    if _WIN_BASH_CACHE is not False:
        return _WIN_BASH_CACHE  # type: ignore[return-value]
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"),
    ]
    chosen: str | None = None
    for c in candidates:
        if c and os.path.isfile(c):
            chosen = c
            break
    if chosen is None:
        found = shutil.which("bash")
        # Skip the WSL launcher (System32\bash.exe) — it execs into the
        # Linux subsystem, where the Windows cwd/paths don't apply.
        if found and "system32" not in found.lower():
            chosen = found
    _WIN_BASH_CACHE = chosen
    return chosen


def _invocation(command: str) -> tuple[str | list[str], bool]:
    """Return ``(args, shell)`` for the host run. POSIX: the command
    string via the host shell (unchanged). Windows: a real bash via
    ``[bash, "-c", command]`` (shell=False) when available, else the
    command string via cmd.exe (shell=True) as a last resort."""
    if sys.platform == "win32":
        bash = _windows_bash()
        if bash:
            return ([bash, "-c", command], False)
    return (command, True)


class LocalBackend(Backend):
    backend_id = "local"

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        args, use_shell = _invocation(command)
        try:
            proc = subprocess.run(
                args,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return RunResult(
                exit_code=-1,
                stdout=decode_maybe(e.stdout),
                stderr=decode_maybe(e.stderr),
                timed_out=True,
            )

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        args, use_shell = _invocation(command)
        return subprocess.Popen(
            args,
            shell=use_shell,
            cwd=cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
