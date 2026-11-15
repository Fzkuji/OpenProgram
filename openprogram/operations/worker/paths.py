"""State file paths for the persistent worker.

Layout (under ``<state-dir>/`` resolved by ``openprogram.state.paths.get_state_dir``):

    worker.lock   — fcntl-locked file; only one worker process at a time
    worker.pid    — PID + start-timestamp written by the running worker
    worker.port   — port the in-process webui WebSocket is listening on
    web-port      — the port this profile chose at its first start and keeps
    worker.log    — combined stdout + stderr from a detached worker
    worker.stop   — PID a stop was asked of, where no signal can ask (Windows)
"""
from __future__ import annotations

from pathlib import Path


def state_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    d = get_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path() -> Path:
    return state_dir() / "worker.lock"


def pid_path() -> Path:
    return state_dir() / "worker.pid"


def port_path() -> Path:
    return state_dir() / "worker.port"


def log_path() -> Path:
    return state_dir() / "worker.log"


def stop_request_path() -> Path:
    return state_dir() / "worker.stop"


def web_port_record_path() -> Path:
    return state_dir() / "web-port"
