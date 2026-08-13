"""Backward-compatible channel worker imports.

Legacy channel and web tests still import worker lifecycle helpers from
``openprogram.channels.worker``. The implementation now lives in
``openprogram.worker``; this shim preserves the old import path.
"""

from openprogram.worker import (
    current_worker_pid,
    print_status,
    read_worker_port,
    restart_worker,
    run_foreground,
    spawn_detached,
    stop_worker,
)

__all__ = [
    "current_worker_pid",
    "print_status",
    "read_worker_port",
    "restart_worker",
    "run_foreground",
    "spawn_detached",
    "stop_worker",
]
