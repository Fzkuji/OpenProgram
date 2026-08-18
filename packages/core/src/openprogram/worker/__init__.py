"""Persistent worker process for OpenProgram.

The worker is a long-running process that hosts the webui WebSocket
server (model calls, sessions, tool execution) and any configured
channel adapters (Discord, Telegram, WeChat, ...). All TUI / Web UI
front-ends connect to this single process, so multiple front-ends and
external channels share state.

Public surface (re-exported here for convenience):

    spawn_detached()       — fork a worker, return immediately
    run_foreground()       — run in the current process; blocks until SIGTERM
    stop_worker()          — SIGTERM the live worker
    restart_worker()       — stop + start
    print_status()         — pretty-printed status (PID, port, uptime)
    current_worker_pid()   — PID of the live worker, or None
    read_worker_port()     — port the live worker is listening on, or None

    services.install()     — install launchd / systemd user service
    services.uninstall()   — remove the service
    services.status()      — service-manager view of the install state
"""
from .lifecycle import (
    current_worker_pid,
    print_status,
    read_worker_port,
    restart_worker,
    spawn_detached,
    stop_worker,
)
from .runner import run_foreground

__all__ = [
    "current_worker_pid",
    "print_status",
    "read_worker_port",
    "restart_worker",
    "run_foreground",
    "spawn_detached",
    "stop_worker",
]
