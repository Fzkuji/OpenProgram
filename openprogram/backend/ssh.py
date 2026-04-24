"""SSH backend — ``ssh <target> "..."`` per call.

Uses the system ``ssh`` client with ``BatchMode=yes`` so password
prompts don't dead-lock the agent loop. The caller is expected to
have set up key-based auth ahead of time.
"""
from __future__ import annotations

import shlex
import subprocess

from openprogram.backend.base import Backend, RunResult, decode_maybe


class SshBackend(Backend):
    backend_id = "ssh"

    def __init__(self, target: str) -> None:
        if not target:
            raise RuntimeError(
                "ssh backend: `backend.ssh_target` is empty. Run "
                "`openprogram config backend` to set user@host."
            )
        self.target = target

    def _ssh_argv(self, command: str, cwd: str | None = None) -> list[str]:
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            self.target,
            command,
        ]

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        try:
            proc = subprocess.run(
                self._ssh_argv(command, cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return RunResult(
                exit_code=-1,
                stdout=decode_maybe(e.stdout),
                stderr=decode_maybe(e.stderr),
                timed_out=True,
            )
        except FileNotFoundError:
            return RunResult(
                exit_code=127,
                stdout="",
                stderr="ssh CLI not on PATH — install OpenSSH or switch "
                       "backend via `openprogram config backend`.",
            )

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        # -T disables pseudo-tty allocation so our PIPEs stay clean
        # line streams. Process output is captured via the remote ssh
        # client streaming stdout back over the same socket.
        argv = ["ssh", "-T",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                self.target,
                f"cd {shlex.quote(cwd)} && {command}" if cwd else command]
        return subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
