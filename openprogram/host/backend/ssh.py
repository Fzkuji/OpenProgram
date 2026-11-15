"""SSH backend — ``ssh <target> "..."`` per call.

Uses the system ``ssh`` client with ``BatchMode=yes`` so password
prompts don't dead-lock the agent loop. The caller is expected to
have set up key-based auth ahead of time.

Calls share one multiplexed connection per target (``ControlMaster``),
so the many small reads and writes a file tool makes don't each pay a
full handshake. ``workdir`` is where commands start when the caller
gives no cwd: an SSH environment's remote folder. Windows' OpenSSH client
has no connection sharing, so there every call opens its own connection.

The remote side speaks UTF-8 whatever this machine's code page is, and the
client never opens a console window of its own.
"""
from __future__ import annotations

import os
import shlex
import subprocess

from openprogram.host.backend.base import Backend, RunResult, decode_maybe
from openprogram.host._platform.process_ownership import no_window_creation_flags


def _control_dir() -> str | None:
    try:
        from openprogram.state.paths import get_state_dir
        path = os.path.join(str(get_state_dir()), "ssh")
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path
    except Exception:
        return None


class SshBackend(Backend):
    backend_id = "ssh"

    def __init__(self, target: str, workdir: str = "") -> None:
        if not target:
            raise RuntimeError(
                "ssh backend: no SSH target. Add an SSH environment in "
                "Settings > Environments or with `openprogram environments add`."
            )
        self.target = target
        self.workdir = workdir

    def _options(self) -> list[str]:
        opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        control = _control_dir()
        if control and os.name != "nt":
            # %C hashes host/port/user, which keeps the socket path short
            # enough for the 104-byte sun_path limit on macOS.
            opts += ["-o", "ControlMaster=auto",
                     "-o", f"ControlPath={control}/%C",
                     "-o", "ControlPersist=300"]
        return opts

    def _remote_command(self, command: str, cwd: str | None) -> str:
        start = cwd or self.workdir
        if not start:
            return command
        # A leading ~ must reach the remote shell unquoted to expand there.
        if start == "~" or start.startswith("~/"):
            quoted = "~" + (shlex.quote(start[1:]) if len(start) > 1 else "")
        else:
            quoted = shlex.quote(start)
        return f"cd {quoted} && {command}"

    def _ssh_argv(self, command: str, cwd: str | None = None) -> list[str]:
        return ["ssh", *self._options(), self.target, self._remote_command(command, cwd)]

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        try:
            proc = subprocess.run(
                self._ssh_argv(command, cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=no_window_creation_flags(),
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
                stderr="ssh CLI not on PATH — install OpenSSH or pick "
                       "another environment.",
            )

    def run_bytes(self, command: str, *, data: bytes | None = None,
                  timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        """Run ``command`` remotely with raw bytes in and out.

        File tools use this to move file contents without any text
        decoding on the way. Returns ``(exit_code, stdout, stderr)``;
        exit code 255 means ssh itself failed (connection, auth).
        """
        try:
            proc = subprocess.run(
                ["ssh", *self._options(), self.target, command],
                input=data if data is not None else b"",
                capture_output=True,
                timeout=timeout,
                creationflags=no_window_creation_flags(),
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, b"", f"ssh timed out after {timeout:.0f}s".encode()
        except FileNotFoundError:
            return 127, b"", b"ssh CLI not on PATH"

    def spawn_spec(self, command: str, cwd: str | None = None) -> dict:
        return {"args": ["ssh", "-T", *self._options(), self.target,
                         self._remote_command(command, cwd)],
                "creationflags": no_window_creation_flags()}

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        # -T disables pseudo-tty allocation so our PIPEs stay clean
        # line streams. Process output is captured via the remote ssh
        # client streaming stdout back over the same socket.
        return subprocess.Popen(
            **self.spawn_spec(command, cwd=cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
