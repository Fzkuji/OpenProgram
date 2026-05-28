# `openprogram/worker/`

> Persistent worker process for OpenProgram.

## Overview

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

## Files in this directory

- **`lifecycle.py`** — Lifecycle controls for the persistent worker process
- **`lock.py`** — Single-holder file lock for the persistent worker
- **`paths.py`** — State file paths for the persistent worker
- **`runner.py`** — Foreground run-loop for the persistent worker
- **`web.py`** — Manage the Next.js frontend subprocess

## Sub-packages

- **`services/`** — System service integration for the persistent worker

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
