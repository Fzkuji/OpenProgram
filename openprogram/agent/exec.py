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
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
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

    process = await asyncio.create_subprocess_exec(command, *args, **kwargs)

    killed = False

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

    cancel_task = None
    if opts.signal:
        async def _watch_cancel():
            await opts.signal.wait()
            kill_proc()
        cancel_task = asyncio.create_task(_watch_cancel())

    timeout_task = None
    if opts.timeout and opts.timeout > 0:
        async def _do_timeout():
            await asyncio.sleep(opts.timeout / 1000)
            kill_proc()
        timeout_task = asyncio.create_task(_do_timeout())

    try:
        stdout_bytes, stderr_bytes = await process.communicate()
    except Exception:
        stdout_bytes, stderr_bytes = b"", b""
    finally:
        if cancel_task:
            cancel_task.cancel()
        if timeout_task:
            timeout_task.cancel()

    code = process.returncode if process.returncode is not None else 0

    return ExecResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        code=code,
        killed=killed,
    )
