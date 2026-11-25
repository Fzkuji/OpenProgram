"""The computer_use backend of this host: macOS, Linux on X11, or Windows."""
from __future__ import annotations

import platform
from typing import Any


def support() -> dict[str, Any]:
    """Whether this host can run computer_use, and why not."""
    system = platform.system()
    if system == "Darwin":
        from .macos import frameworks
        return frameworks.support()
    if system == "Linux":
        from .linux import x11
        return x11.support()
    if system == "Windows":
        from .windows import win32
        return win32.support()
    return {"available": False, "reason": "computer_use supports macOS, Linux (X11) and Windows"}


def backend():
    """The process-wide backend for this host."""
    system = platform.system()
    if system in ("Linux", "Windows"):
        if system == "Linux":
            from .linux import computer as module
            from .linux.x11 import support
        else:
            from .windows import computer as module
            from .windows.win32 import support
        state = support()
        if not state["available"]:
            from .common.base import ComputerUseError
            raise ComputerUseError(state["reason"])
        return module.backend()
    from .macos import computer
    return computer.backend()
