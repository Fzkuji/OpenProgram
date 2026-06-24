"""System-level sandbox — restrict bash process file and network access.

macOS: sandbox-exec (Seatbelt)
Linux: bubblewrap (bwrap)

Controlled by the ``sandbox_enabled`` ContextVar. When enabled AND the
platform tool is available, ``wrap_command`` returns a sandboxed
invocation that restricts the child process to *cwd* writes only.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from contextvars import ContextVar

log = logging.getLogger(__name__)

sandbox_enabled: ContextVar[bool] = ContextVar("sandbox_enabled", default=False)


def is_available() -> bool:
    if sys.platform == "darwin":
        return os.path.exists("/usr/bin/sandbox-exec")
    return shutil.which("bwrap") is not None


def wrap_command(command: str, cwd: str) -> tuple[list[str], bool]:
    """Wrap *command* in a sandbox invocation. Returns ``(args, shell)``."""
    cwd = os.path.realpath(cwd)
    if sys.platform == "darwin":
        profile = _seatbelt_profile(cwd)
        return (["/usr/bin/sandbox-exec", "-p", profile, "/bin/bash", "-c", command], False)
    return (_bwrap_args(command, cwd), False)


def _seatbelt_profile(cwd: str) -> str:
    return (
        "(version 1)\n"
        "(deny default)\n"
        '(allow file-read* (subpath "/"))\n'
        f'(allow file-write* (subpath "{cwd}"))\n'
        '(allow file-write* (subpath "/private/var/folders"))\n'
        '(allow file-write* (subpath "/private/tmp"))\n'
        '(allow file-write* (subpath "/tmp"))\n'
        '(allow process-exec (subpath "/bin") (subpath "/usr/bin")'
        ' (subpath "/usr/local/bin") (subpath "/opt/homebrew"))\n'
        "(allow process-fork)\n"
        "(allow sysctl-read)\n"
        "(allow mach-lookup)\n"
    )


def _bwrap_args(command: str, cwd: str) -> list[str]:
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--bind", cwd, cwd,
        "--tmpfs", "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--unshare-net",
        "--", "bash", "-c", command,
    ]
