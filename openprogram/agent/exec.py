"""
Shared subprocess execution utilities.

Originally ported from pi_coding_agent.core.exec (which mirrors
core/exec.ts). Provides exec_command() with timeout and cancellation.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from openprogram._compat import no_window_creation_flags


@dataclass
class ExecOptions:
    """Options for executing shell commands."""
    signal: asyncio.Event | None = None
    timeout: float | None = None  # milliseconds
    cwd: str | None = None


@dataclass
class ExecResult:
    """Result of executing a shell command."""
    stdout: str
    stderr: str
    code: int
    killed: bool


def _kill_process_tree(pid: int) -> None:
    """Kill a process and its entire child tree."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=no_window_creation_flags(),
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


async def exec_command(
    command: str,
    args: list[str],
    cwd: str,
    options: ExecOptions | None = None,
) -> ExecResult:
    """Execute a shell command with optional timeout and abort signal."""
    opts = options or ExecOptions()

    kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": cwd,
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = no_window_creation_flags()

    process = await asyncio.create_subprocess_exec(command, *args, **kwargs)

    killed = False
    timed_out = False

    def kill_proc():
        nonlocal killed
        if not killed and process.pid is not None:
            killed = True
            _kill_process_tree(process.pid)

            async def _escalate():
                await asyncio.sleep(5)
                try:
                    if sys.platform != "win32":
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                except Exception:
                    pass

            asyncio.create_task(_escalate())

    if opts.signal and opts.signal.is_set():
        kill_proc()

    cancel_job = None
    if opts.signal:
        async def _watch_cancel():
            await opts.signal.wait()
            kill_proc()
        cancel_job = asyncio.create_task(_watch_cancel())

    timeout_task = None
    if opts.timeout and opts.timeout > 0:
        async def _do_timeout():
            nonlocal timed_out
            await asyncio.sleep(opts.timeout / 1000)
            timed_out = True
            kill_proc()
        timeout_task = asyncio.create_task(_do_timeout())

    comm_error: Exception | None = None
    try:
        stdout_bytes, stderr_bytes = await process.communicate()
    except Exception as e:  # noqa: BLE001
        stdout_bytes, stderr_bytes = b"", b""
        comm_error = e
    finally:
        if cancel_job:
            cancel_job.cancel()
        if timeout_task:
            timeout_task.cancel()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    code = process.returncode
    if code is None:
        # We never reaped an exit status (communicate() raised, or the
        # kill left the process unwaited). Killed or not, this is NOT a
        # success — reporting 0 makes the caller treat a timed-out or
        # cancelled command as if it had run cleanly.
        code = 124 if timed_out else 1
    elif killed and code == 0:
        # Killed but the process still managed to exit 0 (raced the
        # signal). The command did not complete on its own terms.
        code = 124 if timed_out else 1

    if timed_out:
        note = f"timed out after {opts.timeout / 1000:g}s"
    elif killed:
        note = "killed (cancelled)"
    elif comm_error is not None:
        note = f"failed to read process output: {comm_error}"
    else:
        note = ""
    if note:
        stderr = (stderr + "\n" if stderr else "") + f"[exec] {note}"

    return ExecResult(
        stdout=stdout,
        stderr=stderr,
        code=code,
        killed=killed,
    )
