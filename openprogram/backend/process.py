"""Collect a local command with execution cancellation and child cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import time

from openprogram.agentic_programming.function import CancelledError, check_cancelled


def run_command(
    args: str | list[str],
    *,
    shell: bool,
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Preserve subprocess.run output/timeout semantics while checking cancellation.

    Each POSIX invocation owns a new process group. Cleanup targets that group
    even if the shell exited while a child still holds an output pipe open.
    """
    try:
        check_cancelled()
    except CancelledError:
        return subprocess.CompletedProcess(
            args, -1, "", "[cancelled before process start]"
        )
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        args,
        shell=shell,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    ) as proc:
        try:
            while True:
                check_cancelled()
                remaining = deadline - time.monotonic()
                try:
                    stdout, stderr = proc.communicate(
                        timeout=max(0, min(0.1, remaining))
                    )
                    return subprocess.CompletedProcess(
                        args, proc.returncode, stdout, stderr
                    )
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(args, timeout)
        except BaseException as exc:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        timeout=5,
                    )
                finally:
                    if proc.poll() is None:
                        proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = proc.communicate()
            if isinstance(exc, CancelledError):
                return subprocess.CompletedProcess(
                    args,
                    proc.returncode or -1,
                    stdout,
                    stderr + "\n[cancelled; process terminated]",
                )
            if isinstance(exc, subprocess.TimeoutExpired):
                exc.output, exc.stderr = stdout, stderr
            raise
