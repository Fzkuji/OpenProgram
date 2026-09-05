"""System service integration for the persistent worker.

macOS uses launchd (per-user LaunchAgents). Linux uses systemd
``--user`` units. Windows uses a least-privilege per-user scheduled task.

Public API:

    install()     — write the service file and load it
    uninstall()   — unload + remove the service file
    status()      — service-manager view (loaded? scheduled to run?)
    is_supported() — current platform has an implementation
"""
from __future__ import annotations

import importlib


def _backend():
    from openprogram._compat import worker_service_backend

    name = worker_service_backend()
    if name is None:
        return None
    return importlib.import_module(f".{name}", __name__)


def is_supported() -> bool:
    return _backend() is not None


def install() -> int:
    backend = _backend()
    if backend is not None:
        return backend.install()
    print("openprogram worker install: no service adapter for this platform.")
    print("Use `openprogram worker start` to run the worker manually.")
    return 1


def uninstall() -> int:
    backend = _backend()
    if backend is not None:
        return backend.uninstall()
    print("openprogram worker uninstall: no service adapter for this platform.")
    return 1


def status() -> int:
    backend = _backend()
    if backend is not None:
        return backend.status()
    print("openprogram worker service: no service adapter for this platform.")
    return 1


def _run_if_installed(action: str) -> int | None:
    """Run a service-manager action when this backend owns the worker.

    ``None`` means no installed service claimed the command, so lifecycle can
    use its ordinary detached-process implementation.  A non-zero result is a
    real manager failure and must never silently fall back to a detached worker.
    """

    backend = _backend()
    method = getattr(backend, action, None) if backend is not None else None
    if method is None:
        return None
    return method()


def start_if_installed() -> int | None:
    return _run_if_installed("start_if_installed")


def stop_if_installed() -> int | None:
    return _run_if_installed("stop_if_installed")


def restart_if_installed() -> int | None:
    return _run_if_installed("restart_if_installed")
