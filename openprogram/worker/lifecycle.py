"""Lifecycle controls for the persistent worker process.

Mirrors the channels worker (``openprogram.channels.worker``) but
generalized: the worker now hosts the webui WebSocket server even when
no channel is configured. Channel polling remains an optional add-on.

Public functions:

    spawn_detached()      — fork a background worker, return immediately
    stop_worker()         — SIGTERM the live worker (escalates to SIGKILL)
    restart_worker()      — stop + spawn_detached
    print_status()        — human-readable status report
    current_worker_pid()  — PID of the live worker, or None
    read_worker_port()    — port from worker.port if worker is alive
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from . import paths
from .lock import read_holder_pid


# ── port file ────────────────────────────────────────────────────────────────


def write_port_file(port: int) -> None:
    paths.port_path().write_text(f"{port}\n")


def clear_port_file() -> None:
    try:
        paths.port_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_worker_port() -> Optional[int]:
    """Return the port the live worker's webui is listening on, or None.

    Returns None if there's no live worker, no port file, or the file
    can't be parsed. Verifies liveness so a stale port file from a
    crashed prior worker doesn't get handed out.
    """
    if current_worker_pid() is None:
        return None
    p = paths.port_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


# ── pid file ─────────────────────────────────────────────────────────────────


def _read_pid_file() -> Optional[int]:
    p = paths.pid_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text().strip().splitlines()
        return int(raw[0]) if raw else None
    except (OSError, ValueError):
        return None


def write_pid_file() -> None:
    """Write current PID + start timestamp. Called by the running worker."""
    paths.pid_path().write_text(f"{os.getpid()}\n{int(time.time())}\n")


def clear_pid_file() -> None:
    try:
        paths.pid_path().unlink(missing_ok=True)
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def current_worker_pid() -> Optional[int]:
    """Return the PID of the live worker, or None.

    Prefers the lock file (authoritative — fcntl-backed). Falls back
    to the .pid sidecar in case the lock got cleared by a clean
    release while the worker process kept running (defensive).
    """
    holder = read_holder_pid()
    if holder is not None and _process_alive(holder):
        return holder
    pid = _read_pid_file()
    if pid is not None and _process_alive(pid):
        return pid
    return None


# ── start / stop ─────────────────────────────────────────────────────────────


def spawn_detached() -> int:
    """Fork a background worker. Returns 0 on success, 1 if already running."""
    existing = current_worker_pid()
    if existing is not None:
        port = read_worker_port()
        port_str = f", port {port}" if port else ""
        print(
            f"openprogram worker already running (PID {existing}{port_str}). "
            f"Stop it first with `openprogram worker stop`."
        )
        return 1

    log_file = paths.log_path()
    # -u: unbuffered, so 'worker status' shows fresh output immediately.
    # --foreground: re-exec must not loop back through spawn_detached.
    cmd = [sys.executable, "-u", "-m", "openprogram", "worker", "run"]
    log = open(log_file, "a", buffering=1)
    log.write(f"\n--- worker starting at {time.ctime()} ---\n")
    log.flush()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=Path.home(),
        )
    except Exception as e:  # noqa: BLE001
        log.close()
        print(f"failed to spawn worker: {type(e).__name__}: {e}")
        return 1

    deadline = time.time() + 5.0
    while time.time() < deadline:
        time.sleep(0.2)
        rc = proc.poll()
        if rc is not None:
            print(f"worker exited immediately (rc={rc}). Tail of {log_file}:")
            try:
                lines = log_file.read_text().splitlines()[-20:]
                for line in lines:
                    print(f"  {line}")
            except OSError:
                pass
            return 1
        if current_worker_pid() == proc.pid:
            port = read_worker_port()
            port_str = f", port {port}" if port else ""
            print(f"openprogram worker started (PID {proc.pid}{port_str}). Logs: {log_file}")
            return 0

    print(f"openprogram worker starting (PID {proc.pid}); not yet ready. Watch {log_file}.")
    return 0


def stop_worker() -> int:
    """SIGTERM the worker, escalate to SIGKILL after 5s. Returns 0 on success."""
    pid = current_worker_pid()
    if pid is None:
        print("openprogram worker: not running.")
        return 0
    print(f"Stopping openprogram worker (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("Process already gone.")
        clear_pid_file()
        clear_port_file()
        return 0
    except PermissionError:
        print(f"Can't signal PID {pid} — owned by another user.")
        return 1

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _process_alive(pid):
            print("Stopped.")
            return 0
        time.sleep(0.2)

    print(f"PID {pid} didn't exit after SIGTERM; sending SIGKILL.")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        clear_pid_file()
        clear_port_file()
        print("Stopped.")
        return 0
    except OSError:
        return 1

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _process_alive(pid):
            clear_pid_file()
            clear_port_file()
            print("Stopped.")
            return 0
        time.sleep(0.1)
    print(f"PID {pid} is still alive after SIGKILL.")
    return 1


def restart_worker() -> int:
    """Stop the live worker (if any) and spawn a fresh one."""
    if current_worker_pid() is not None:
        rc = stop_worker()
        if rc != 0:
            return rc
        # Wait a beat for the lock + port files to clear.
        deadline = time.time() + 2.0
        while time.time() < deadline and current_worker_pid() is not None:
            time.sleep(0.1)
    return spawn_detached()


# ── status ───────────────────────────────────────────────────────────────────


def _worker_start_time(pid: int) -> Optional[float]:
    p = paths.pid_path()
    try:
        raw = p.read_text().strip().splitlines()
        if len(raw) >= 2 and int(raw[0]) == pid:
            return float(raw[1])
    except (OSError, ValueError):
        pass
    return None


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60)}m"
    return f"{int(seconds // 86400)}d{int((seconds % 86400) // 3600)}h"


def print_status() -> int:
    """One-screen status report for the worker."""
    pid = current_worker_pid()
    if pid is None:
        print("openprogram worker: not running")
        print()
        print("  Start it with:  openprogram worker start")
        print("  Or install as a service:  openprogram worker install")
        return 0

    started = _worker_start_time(pid)
    age = ""
    if started is not None:
        age = f", up {_format_duration(time.time() - started)}"

    port = read_worker_port()
    port_str = f", port {port}" if port else ""
    print(f"openprogram worker: running (PID {pid}{port_str}{age})")
    print(f"  logs: {paths.log_path()}")

    try:
        from openprogram.channels import list_status
        rows = list_status()
        active = [
            r for r in rows
            if r.get("enabled") and r.get("implemented") and r.get("configured")
        ]
        if active:
            labels = [f"{r['platform']}:{r['account_id']}" for r in active]
            print(f"  channels: {', '.join(labels)}")
        else:
            print("  channels: none configured")
    except Exception:
        pass
    return 0
