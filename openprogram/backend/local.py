"""Local backend — bounded subprocess execution in the host shell.

The agent's ``bash`` tool routes every command through here. On POSIX
that's ``shell=True`` (the host ``/bin/sh``), exactly as before. On
Windows ``shell=True`` would invoke ``cmd.exe``, which cannot parse the
bash syntax the agent is steered toward (``&&``, pipes, ``$(...)``,
single-quote escaping, heredocs) or run unix coreutils (``rm``/``ls``/
``grep``/…). So on Windows we run the command through Git Bash when it is
present, falling back to the built-in Windows PowerShell instead of the much
less capable ``cmd.exe``. Background shells are created without a console
window so Desktop tool calls do not flash a terminal on screen.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import replace

from openprogram.backend.base import Backend, RunResult, decode_maybe
from openprogram.backend.process import run_command
from openprogram import sandbox as _sandbox
from openprogram._compat import (
    ProcessTreeOwner,
    no_window_creation_flags,
)

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


def _windows_powershell() -> str:
    """Return the best available non-interactive Windows PowerShell."""

    found = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if found:
        return found
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def _invocation(command: str, cwd: str | None = None, *,
                policy: _sandbox.SandboxPolicy | None = None,
                force_sandbox: bool = False,
                ) -> tuple[str | list[str], bool, dict | None, bool]:
    """Return ``(args, shell, env, sandboxed)`` for the host run. POSIX: the command
    string via the host shell (unchanged). Windows: a real bash via
    ``[bash, "-c", command]`` (shell=False) when available, else the
    command string via non-interactive Windows PowerShell. ``env`` is None
    when the command runs unsandboxed, i.e. inherits ours.

    With ``sandbox.mode`` set, the command is wrapped so the child can
    only write inside *cwd*, cannot read the configured credential paths,
    and gets a filtered environment. The policy is read from the config
    on every call, so it holds in threads and subprocesses alike.

    Raises ``SandboxUnavailable`` when a sandbox is configured, the
    platform tool is missing, and ``sandbox.unavailable_policy`` is
    ``refuse`` — silently running the command unprotected is how a
    security setting turns into a placebo.
    """
    if policy is None:
        policy = (_sandbox.resolve_policy(required=True) if force_sandbox
                  else _sandbox.resolve_policy())
    from openprogram.sandbox.recoverable_delete import (
        TRASH_ENV,
        prepare_child_env,
        sandbox_writable_root,
    )
    if policy is not None:
        trash_root = sandbox_writable_root()
        if trash_root:
            prepared_env = prepare_child_env(_sandbox.child_env(policy))
            policy = replace(
                policy,
                writable_roots=policy.writable_roots + (trash_root,),
            )
        else:
            prepared_env = _sandbox.child_env(policy)
        reason = _sandbox.unavailable_reason()
        if reason is None:
            args, shell = _sandbox.wrap_command(command, cwd or os.getcwd(), policy)
            return (args, shell, prepared_env, True)
        if (force_sandbox
                or _sandbox.unavailable_policy() == _sandbox.UNAVAILABLE_POLICY_REFUSE):
            raise _sandbox.SandboxUnavailable(
                f"sandbox.mode is on but the sandbox cannot run here: {reason}. "
                "Install it, or set sandbox.unavailable_policy=warn to run without "
                "one, or set sandbox.mode=danger-full-access."
            )
        log.warning("sandbox requested but unavailable (%s) — running "
                    "the command WITHOUT a sandbox", reason)
    if sys.platform == "win32":
        bash = _windows_bash()
        if bash:
            prepared_env = prepare_child_env()
            if prepared_env and prepared_env.get(TRASH_ENV):
                # MSYS prepends /usr/bin to a Windows PATH while starting
                # Git Bash, which would otherwise put the real rm/rmdir ahead
                # of our recoverable-delete shims. Resolve the run-local path
                # inside MSYS and prepend it again in the shell itself.
                command = (
                    'PATH="$(cygpath -u "$OPENPROGRAM_RECOVERABLE_TRASH")'
                    '/shims/bin:$PATH"; export PATH; ' + command
                )
            return ([bash, "-c", command], False, prepared_env, False)
        return (
            [
                _windows_powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            False,
            prepare_child_env(),
            False,
        )
    return (command, True, prepare_child_env(), False)


class LocalBackend(Backend):
    backend_id = "local"

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        try:
            args, use_shell, env, sandboxed = _invocation(command, cwd=cwd)
        except _sandbox.SandboxUnavailable as e:
            # The Backend contract is to report failures as a result, not
            # to raise them at the tool.
            return RunResult(
                exit_code=1, stdout="", stderr=str(e),
                sandbox_error="unavailable",
            )
        try:
            proc = run_command(
                args,
                tree=ProcessTreeOwner(),
                shell=use_shell,
                timeout=timeout,
                cwd=cwd,
                env=env,
            )
            sandbox_error = (
                "denied" if _sandbox.is_sandbox_denial(
                    proc.returncode, proc.stdout, proc.stderr,
                    sandboxed=sandboxed,
                ) else None
            )
            path = rule = None
            if sandbox_error == "denied":
                hit = _sandbox.match_deny_read(
                    f"{command}\n{proc.stdout}\n{proc.stderr}",
                )
                if hit:
                    path, rule = hit
            return RunResult(
                proc.returncode, proc.stdout, proc.stderr,
                sandbox_error=sandbox_error,
                sandbox_path=path,
                sandbox_rule=rule,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(
                exit_code=-1,
                stdout=decode_maybe(e.stdout),
                stderr=decode_maybe(e.stderr),
                timed_out=True,
            )

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        args, use_shell, env, sandboxed = _invocation(command, cwd=cwd)
        proc = subprocess.Popen(
            args,
            shell=use_shell,
            cwd=cwd or None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=no_window_creation_flags(),
        )
        setattr(proc, "_openprogram_sandboxed", sandboxed)
        return proc
