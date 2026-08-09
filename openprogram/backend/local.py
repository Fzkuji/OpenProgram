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

import logging
import os
import shutil
import subprocess
import sys

from openprogram.backend.base import Backend, RunResult, decode_maybe
from openprogram import sandbox as _sandbox

log = logging.getLogger(__name__)

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


def _invocation(command: str, cwd: str | None = None
                ) -> tuple[str | list[str], bool, dict | None]:
    """Return ``(args, shell, env)`` for the host run. POSIX: the command
    string via the host shell (unchanged). Windows: a real bash via
    ``[bash, "-c", command]`` (shell=False) when available, else the
    command string via cmd.exe (shell=True) as a last resort. ``env`` is
    None when the command runs unsandboxed, i.e. inherits ours.

    With ``sandbox.mode`` set, the command is wrapped so the child can
    only write inside *cwd*, cannot read the configured credential paths,
    and gets a filtered environment. The policy is read from the config
    on every call, so it holds in threads and subprocesses alike.

    Raises ``SandboxUnavailable`` when a sandbox is configured, the
    platform tool is missing, and ``sandbox.on_unavailable`` is
    ``refuse`` — silently running the command unprotected is how a
    security setting turns into a placebo.
    """
    policy = _sandbox.resolve_policy()
    if policy is not None:
        reason = _sandbox.unavailable_reason()
        if reason is None:
            args, shell = _sandbox.wrap_command(command, cwd or os.getcwd(), policy)
            return (args, shell, _sandbox.child_env(policy))
        if _sandbox.on_unavailable() == _sandbox.ON_UNAVAILABLE_REFUSE:
            raise _sandbox.SandboxUnavailable(
                f"sandbox.mode is on but the sandbox cannot run here: {reason}. "
                "Install it, or set sandbox.on_unavailable=warn to run without "
                "one, or set sandbox.mode=off."
            )
        log.warning("sandbox requested but unavailable (%s) — running "
                    "the command WITHOUT a sandbox", reason)
    if sys.platform == "win32":
        bash = _windows_bash()
        if bash:
            return ([bash, "-c", command], False, None)
    return (command, True, None)


class LocalBackend(Backend):
    backend_id = "local"

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        try:
            args, use_shell, env = _invocation(command, cwd=cwd)
        except _sandbox.SandboxUnavailable as e:
            # The Backend contract is to report failures as a result, not
            # to raise them at the tool.
            return RunResult(exit_code=1, stdout="", stderr=str(e))
        try:
            proc = subprocess.run(
                args,
                shell=use_shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=env,
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
        args, use_shell, env = _invocation(command, cwd=cwd)
        return subprocess.Popen(
            args,
            shell=use_shell,
            cwd=cwd or None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
