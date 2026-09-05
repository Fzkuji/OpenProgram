"""Collect a local command with execution cancellation and child cleanup."""

from __future__ import annotations

import subprocess
import time

from openprogram._compat import ProcessTreeOwner
from openprogram.agentic_programming.function import CancelledError, check_cancelled
from openprogram.backend.base import decode_maybe


def run_command(
    args: str | list[str],
    *,
    tree: ProcessTreeOwner,
    shell: bool,
    timeout: float,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Collect an owned process tree, preserving bounded timeout/cleanup behavior."""
    try:
        check_cancelled()
    except CancelledError:
        return subprocess.CompletedProcess(
            args, -1, "", "[cancelled before process start]"
        )
    deadline = time.monotonic() + timeout
    proc = tree.popen(
        args,
        shell=shell,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic()
            try:
                stdout, stderr = proc.communicate(timeout=max(0, min(0.1, remaining)))
                tree.release()
                return subprocess.CompletedProcess(
                    args, proc.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired as exc:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        args, timeout, exc.stdout, exc.stderr
                    )
    except BaseException as exc:
        terminated = tree.terminate()
        if not terminated:
            try:
                proc.kill()
            except OSError:
                pass
        drained = False
        try:
            stdout, stderr = proc.communicate(timeout=5)
            drained = True
        except subprocess.TimeoutExpired as drain_error:
            # Never close pipes held by communicate's Windows reader thread.
            # A bounded drain may return partial output, but does not confirm
            # that a cancelled tool's effects have finished.
            try:
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            stdout = decode_maybe(
                drain_error.stdout
                if drain_error.stdout is not None
                else getattr(exc, "stdout", None)
            )
            stderr = decode_maybe(
                drain_error.stderr
                if drain_error.stderr is not None
                else getattr(exc, "stderr", None)
            )
        if isinstance(exc, CancelledError) and terminated and drained:
            return subprocess.CompletedProcess(
                args,
                proc.returncode or -1,
                stdout or "",
                (stderr or "") + "\n[cancelled; process terminated]",
            )
        if isinstance(exc, subprocess.TimeoutExpired):
            exc.output, exc.stderr = stdout, stderr
        raise
