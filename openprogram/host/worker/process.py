"""Which process is this profile's worker, the port it listens on, the
interpreter it runs as.

These are facts of the machine and the profile's state directory, read the
same way by every layer: the gateway's address (``models``), the native
check's executor (``host.system_access``) and the worker's own lifecycle
(``operations.worker``). Starting, stopping and claiming a port stay with
the lifecycle; this module only reads.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from . import paths
from .lock import is_held_by, read_holder_pid

_log = logging.getLogger(__name__)


def explicit_web_port() -> tuple[int, str] | None:
    """A port the user set, with where it was set; None when none was."""
    raw = os.environ.get("OPENPROGRAM_WEB_PORT", "").strip()
    if raw:
        try:
            return int(raw), "OPENPROGRAM_WEB_PORT"
        except ValueError:
            pass
    try:
        from openprogram.state.config import read_config
        ui = read_config().get("ui") or {}
        if ui.get("web_port"):
            return int(ui["web_port"]), "ui.web_port"
    except Exception:
        _log.debug("web_port from config unreadable", exc_info=True)
    return None


def recorded_web_port() -> Optional[int]:
    """The port this profile chose at its first start, or None before that."""
    try:
        port = int(paths.web_port_record_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def resolve_worker_port() -> int:
    """This profile's port (docs/reference/design/cli/single-port.html).

    Priority: ``OPENPROGRAM_WEB_PORT`` → setting ``ui.web_port`` → the port
    the profile recorded at its first start (``web-port``) → 18100. Reads
    only; a starting worker settles the port with :func:`claim_worker_port`.
    """
    explicit = explicit_web_port()
    if explicit is not None:
        return explicit[0]
    from .ports import FIRST_WEB_PORT

    return recorded_web_port() or FIRST_WEB_PORT


def _process_alive(pid: int) -> bool:
    from openprogram.host._platform.processes import process_alive
    return process_alive(pid)


def current_worker_pid() -> Optional[int]:
    """Return the PID of the live worker, or None.

    The bytes in ``worker.lock`` are only a hint: a crashed process leaves
    them behind even though the kernel has released the advisory lock, and
    that PID may later be reused by an unrelated process.  Verify actual lock
    ownership before trusting the holder.  The holder's PID reads on every
    platform while the lock is held (host/_platform/locking.py), so the lock
    is the only proof; ``worker.pid`` is never trusted on its own.
    """
    holder = read_holder_pid()
    if (
        holder is not None
        and _process_alive(holder)
        and is_held_by(holder)
    ):
        return holder
    return None


def read_worker_port() -> Optional[int]:
    """Return the port this profile's live worker listens on, or None.

    Only the worker's own ``worker.port`` counts, and only while the worker
    holds its lock: a listener is not this profile's worker just because it
    answers on the expected port (another account's worker, or another
    program, may hold it).
    """
    if current_worker_pid() is None:
        return None
    try:
        return int(paths.port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def worker_executable() -> str:
    """Use the named macOS runtime declared by this managed installation."""
    from openprogram.host._platform.runtime_layout import managed

    if sys.platform != "darwin" or managed():
        return sys.executable
    import json
    executable = Path(sys.executable).resolve()
    for root in executable.parents:
        manifest_path = root / "runtime-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = manifest.get("worker_python")
        if not relative:
            return sys.executable  # Legacy/source installs keep their interpreter.
        helper = (root / relative).resolve()
        managed = (root / manifest["python"]).resolve()
        if executable not in {managed, helper}:
            return sys.executable
        try:
            helper.relative_to(root)
        except ValueError:
            raise RuntimeError("Named runtime escapes the managed installation") from None
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise RuntimeError("Named runtime is missing; repair the installation")
        return str(helper)
    return sys.executable
