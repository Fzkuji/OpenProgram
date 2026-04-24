"""Docker backend — ``docker run --rm`` per call.

Per-call container spawn keeps the implementation stateless (no
container lifecycle to manage) at the cost of startup overhead per
bash invocation. For bash-heavy agent workflows the user should
stick with ``local`` or run a dedicated long-lived container and
use ``ssh`` into it; a long-lived docker pool is future work.
"""
from __future__ import annotations

import subprocess

from openprogram.backend.base import Backend, RunResult, decode_maybe


class DockerBackend(Backend):
    backend_id = "docker"

    def __init__(self, image: str = "ubuntu:24.04") -> None:
        self.image = image

    def _argv(self, command: str, cwd: str | None = None) -> list[str]:
        argv = ["docker", "run", "--rm", "-i"]
        if cwd:
            argv += ["-w", cwd]
        argv += [self.image, "sh", "-c", command]
        return argv

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        try:
            proc = subprocess.run(
                self._argv(command, cwd),
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
                stderr="docker CLI not on PATH — install Docker or "
                       "switch backend via `openprogram config backend`.",
            )

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        # Per-spawn container; caller manages lifecycle via the returned
        # Popen. Terminating the docker client tears the container down
        # since --rm is set.
        return subprocess.Popen(
            self._argv(command, cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
