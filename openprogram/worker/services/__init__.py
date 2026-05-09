"""System service integration for the persistent worker.

macOS uses launchd (per-user LaunchAgents). Linux uses systemd
``--user`` units. Windows is unsupported here — users on Windows should
use ``openprogram worker start`` and Task Scheduler to auto-start.

Public API:

    install()     — write the service file and load it
    uninstall()   — unload + remove the service file
    status()      — service-manager view (loaded? scheduled to run?)
    is_supported() — current platform has an implementation
"""
from __future__ import annotations

import sys


def is_supported() -> bool:
    return sys.platform in ("darwin", "linux")


def install() -> int:
    if sys.platform == "darwin":
        from . import launchd
        return launchd.install()
    if sys.platform == "linux":
        from . import systemd
        return systemd.install()
    print(f"openprogram worker install: {sys.platform} not supported.")
    print("Use `openprogram worker start` to run the worker manually.")
    return 1


def uninstall() -> int:
    if sys.platform == "darwin":
        from . import launchd
        return launchd.uninstall()
    if sys.platform == "linux":
        from . import systemd
        return systemd.uninstall()
    print(f"openprogram worker uninstall: {sys.platform} not supported.")
    return 1


def status() -> int:
    if sys.platform == "darwin":
        from . import launchd
        return launchd.status()
    if sys.platform == "linux":
        from . import systemd
        return systemd.status()
    print(f"openprogram worker service: {sys.platform} not supported.")
    return 1
