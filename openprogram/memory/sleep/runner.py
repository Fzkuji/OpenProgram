"""Orchestrate light → deep → REM and own the sleep lock."""
from __future__ import annotations

import fcntl
import logging
import os
from typing import Any, Callable

from .. import store
from . import deep, light, rem

logger = logging.getLogger(__name__)


def _try_lock() -> int | None:
    """Acquire the sleep lock or return None if another sweep is running."""
    fd = os.open(str(store.sleep_lock_path()), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def run_sweep(*, llm: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    """Run all three phases in order. Holds the sleep lock for the duration."""
    fd = _try_lock()
    if fd is None:
        return {"skipped": "another sweep is running"}
    try:
        report: dict[str, Any] = {}
        report["light"] = light.run()
        report["deep"] = deep.run(llm=llm)
        report["rem"] = rem.run(llm=llm)
        return report
    finally:
        _release_lock(fd)


def run_phase(name: str, *, llm: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    fd = _try_lock()
    if fd is None:
        return {"skipped": "another sweep is running"}
    try:
        if name == "light":
            return light.run()
        if name == "deep":
            return deep.run(llm=llm)
        if name == "rem":
            return rem.run(llm=llm)
        raise ValueError(f"unknown sleep phase: {name!r}")
    finally:
        _release_lock(fd)
