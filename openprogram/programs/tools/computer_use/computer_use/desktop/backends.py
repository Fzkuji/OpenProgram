"""The computer_use backend of this host: macOS, Linux on X11, or Windows."""
from __future__ import annotations

import platform
from typing import Any


def support() -> dict[str, Any]:
    """Whether this host can run computer_use, and why not."""
    system = platform.system()
    if system == "Darwin":
        from . import macos
        return macos.support()
    if system == "Linux":
        from . import linux
        return linux.support()
    if system == "Windows":
        from . import windows
        return windows.support()
    return {"available": False, "reason": "computer_use supports macOS, Linux (X11) and Windows"}


def backend():
    """The process-wide backend for this host."""
    system = platform.system()
    if system in ("Linux", "Windows"):
        if system == "Linux":
            from . import linux as module
        else:
            from . import windows as module
        state = module.support()
        if not state["available"]:
            from .base import ComputerUseError
            raise ComputerUseError(state["reason"])
        return module.backend()
    from . import macos
    return macos.backend()
