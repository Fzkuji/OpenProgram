"""Which process may migrate an execution store's schema.

A profile's store lives in its state directory next to ``worker.lock``,
the advisory lock the profile's single worker holds. The process that holds
that lock — the running worker, or a process that took it to stand in for
an absent worker — owns the store's schema. Any other process may read and
write a store already at its code's schema, but it never upgrades an older
one: an older worker could still be writing rows in the old shape.

A store with no ``worker.lock`` beside it (a scratch path, a test) has never
been served by a worker, so whoever opens it owns it.
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path
import logging

_log = logging.getLogger(__name__)


def owns_store_schema(store_path: Path) -> bool:
    from openprogram.operations.worker.lock import held_in_this_process

    lock = Path(store_path).parent / "worker.lock"
    if held_in_this_process(lock):
        return True
    return not lock.exists()


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("openprogram")
    except Exception:
        return "unknown"


@functools.lru_cache(maxsize=1)
def _source_revision() -> str:
    """The commit this process's code was built from, or ``""``.

    Read once per process: the code already imported is what it is, even if
    a checkout's ``HEAD`` moves later.
    """
    import openprogram

    package = Path(openprogram.__file__).resolve().parent
    marker = package / "_build_revision.txt"
    try:
        if marker.is_file() and marker.stat().st_size < 128:
            text = marker.read_text(encoding="ascii").strip()
            if re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", text):
                return text
        elif (package.parent / ".git").exists():
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(package.parent),
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        # Unreadable marker or git unavailable: the revision stays unknown.
        _log.debug("code revision unavailable", exc_info=True)
    return ""


def code_revision() -> str | None:
    """The source revision, else the package version, else ``None``."""
    revision = _source_revision()
    if revision:
        return revision
    package_version = _package_version()
    return None if package_version == "unknown" else f"openprogram {package_version}"


def code_identity() -> str:
    """Package version, source revision and pid, for migration log lines."""
    revision = _source_revision()
    return (
        f"openprogram {_package_version()} "
        f"({revision[:12] or 'unknown revision'}, pid {os.getpid()})"
    )
