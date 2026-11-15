"""Local backend — bounded subprocess execution in the host shell.

The agent's ``bash`` tool routes every command through here, in the shell
``host._platform.command_shell`` resolves: bash on POSIX (``/bin/sh`` only
on a host with no bash), Git Bash or PowerShell on Windows, never
``cmd.exe``, and ``/bin/bash`` under the sandbox. Background shells are
created without a console window so Desktop tool calls do not flash a
terminal on screen.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import replace

from openprogram.host.backend.base import Backend, RunResult, decode_maybe
from openprogram.host.backend.process import run_command
from openprogram.host import sandbox as _sandbox
from openprogram.host._platform.process_ownership import ProcessTreeOwner, no_window_creation_flags
from openprogram.host._platform import command_shell
from openprogram.host._platform.terminal import git_bash_invocation, powershell_invocation

log = logging.getLogger(__name__)


def _invocation(command: str, cwd: str | None = None, *,
                policy: _sandbox.SandboxPolicy | None = None,
                ) -> tuple[str | list[str], bool, dict | None, bool]:
    """Return ``(args, shell, env, sandboxed)`` for the host run. POSIX:
    ``[bash, "-c", command]``, or the command string via ``/bin/sh`` on a
    host without bash. Windows: a real bash via
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
        policy = _sandbox.resolve_policy()
    from openprogram.host.sandbox.recoverable_delete import (
        prepare_child_env,
        sandbox_writable_root,
    )
    if command_shell.resolve_shell(policy=policy).sandboxed:
        trash_root = sandbox_writable_root()
        if trash_root:
            prepared_env = prepare_child_env(_sandbox.child_env(policy))
            policy = replace(
                policy,
                writable_roots=policy.writable_roots + (trash_root,),
            )
        else:
            prepared_env = _sandbox.child_env(policy)
        args, shell = _sandbox.wrap_command(command, cwd or os.getcwd(), policy)
        return (args, shell, prepared_env, True)
    if policy is not None:
        # Configured, but resolve_shell found the sandbox cannot run here.
        reason = _sandbox.unavailable_reason()
        if (policy.network_domains is not None
                or _sandbox.unavailable_policy() == _sandbox.UNAVAILABLE_POLICY_REFUSE):
            raise _sandbox.SandboxUnavailable(
                f"sandbox.mode is on but the sandbox cannot run here: {reason}. "
                "Install it, or set sandbox.unavailable_policy=warn to run without "
                "one, or set sandbox.mode=danger-full-access."
            )
        log.warning("sandbox requested but unavailable (%s) — running "
                    "the command WITHOUT a sandbox", reason)
    args, shell, prepared_env = host_shell_invocation(command, prepare_child_env())
    return (args, shell, prepared_env, False)


def host_shell_invocation(command: str, env: dict | None = None, *,
                          profile: command_shell.ShellProfile | None = None,
                          ) -> tuple[str | list[str], bool, dict | None]:
    """``(args, shell, env)`` that run ``command`` in the host shell
    (``command_shell.host_shell``), or in ``profile`` when one is given.

    The one place that turns shell source into a process on the host, so a
    user's hook runs in the shell the command tool uses; a hook declared for
    PowerShell passes that profile. ``env`` is the child's whole
    environment, or None to inherit ours.
    """
    from openprogram.host.sandbox.recoverable_delete import TRASH_ENV
    profile = profile or command_shell.host_shell()
    if profile.kind == "git-bash":
        if env and env.get(TRASH_ENV):
            # MSYS prepends /usr/bin to a Windows PATH while starting the
            # shell, which would put the real rm/rmdir ahead of the
            # recoverable-delete shims; the shell puts them first again.
            # cygpath spells the folder for the bash it sits beside (Cygwin
            # names drives /cygdrive/c); a bash without one gets the MSYS
            # spelling.
            fallback = command_shell.msys_path(env[TRASH_ENV]).rstrip("/")
            command = (
                f'PATH="$(cygpath -u "${TRASH_ENV}" 2>/dev/null'
                f' || printf %s {shlex.quote(fallback)})/shims/bin:$PATH"; export PATH; '
                + command
            )
        args, child_env = git_bash_invocation(profile.path, command, env)
        return (args, False, child_env)
    if profile.is_powershell:
        args, child_env = powershell_invocation(profile.path, command, env)
        return (args, False, child_env)
    if profile.kind == "bash":
        return ([profile.path, "-c", command], False, env)
    return (command, True, env)


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
            if sandbox_error == "denied" and _sandbox.is_network_denial(
                proc.returncode, proc.stdout, proc.stderr, sandboxed=sandboxed,
            ):
                rule = "network"
            elif sandbox_error == "denied":
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

    def spawn_spec(self, command: str, cwd: str | None = None) -> dict:
        args, use_shell, env, sandboxed = _invocation(command, cwd=cwd)
        return {"args": args, "shell": use_shell, "cwd": cwd or None,
                "env": env, "creationflags": no_window_creation_flags(),
                "sandboxed": sandboxed}

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        spec = self.spawn_spec(command, cwd=cwd)
        sandboxed = spec.pop("sandboxed")
        proc = subprocess.Popen(
            **spec,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        setattr(proc, "_openprogram_sandboxed", sandboxed)
        return proc
