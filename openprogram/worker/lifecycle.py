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
    paths.port_path().write_text(f"{port}\n", encoding="utf-8")


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

    Also falls back to a TCP probe of the default port (8109) so a
    foreground ``openprogram web`` — which doesn't write the
    lock/pid/port files — is still discoverable by
    HTTP-client commands like ``openprogram mcp list``. Returns the
    port even if we can't name the PID owning it; callers that need
    the PID can use :func:`find_running_webui` instead.
    """
    if current_worker_pid() is not None:
        p = paths.port_path()
        if p.exists():
            try:
                return int(p.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass
    # Fallback: probe the conventional default port. An unmanaged
    # ``--web`` foreground process is just as serviceable to an HTTP
    # client as a managed worker.
    port = _DEFAULT_WEBUI_PORT
    if _probe_tcp_listening(port):
        return port
    return None


_DEFAULT_WEBUI_PORT = 8109


def _probe_tcp_listening(port: int, host: str = "127.0.0.1",
                         timeout_s: float = 0.4) -> bool:
    """Cheap TCP-connect probe. True if something accepted; False on
    refusal, timeout, or any other socket error.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_s)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def find_running_webui() -> tuple[Optional[int], Optional[int], str]:
    """Locate any webui the user has running. Returns (port, pid, source).

    Three states:

    - ``(port, pid, "managed")`` — ``worker.lock`` + ``worker.pid`` say
      a worker is alive; this is the well-supported path.
    - ``(8109, None, "unmanaged")`` — no lock/pid, but a process is
      listening on the conventional default port. Almost always a
      foreground ``openprogram web``. The PID isn't resolved
      cross-platform-cheaply, so callers that just want to talk
      HTTP get a usable answer.
    - ``(None, None, "none")`` — nothing is up.
    """
    pid = current_worker_pid()
    if pid is not None:
        p = paths.port_path()
        if p.exists():
            try:
                return int(p.read_text(encoding="utf-8").strip()), pid, "managed"
            except (OSError, ValueError):
                pass
        # PID present but no port file — uncommon but treat as managed
        # at the default port (we know SOMETHING owns the lock).
        if _probe_tcp_listening(_DEFAULT_WEBUI_PORT):
            return _DEFAULT_WEBUI_PORT, pid, "managed"
    if _probe_tcp_listening(_DEFAULT_WEBUI_PORT):
        return _DEFAULT_WEBUI_PORT, None, "unmanaged"
    return None, None, "none"


# ── pid file ─────────────────────────────────────────────────────────────────


def _read_pid_file() -> Optional[int]:
    p = paths.pid_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip().splitlines()
        return int(raw[0]) if raw else None
    except (OSError, ValueError):
        return None


def write_pid_file() -> None:
    """Write current PID + start timestamp. Called by the running worker."""
    paths.pid_path().write_text(f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")


def clear_pid_file() -> None:
    try:
        paths.pid_path().unlink(missing_ok=True)
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    """Cross-platform "is process ``pid`` still running?" probe.

    POSIX uses the conventional ``os.kill(pid, 0)`` no-op signal —
    ``ProcessLookupError`` means gone, ``PermissionError`` means alive
    but owned by another user (still "exists"), success means alive.

    Windows doesn't support signal 0. ``os.kill(pid, 0)`` raises
    ``OSError [WinError 87] The parameter is incorrect`` regardless
    of whether the process exists, which would make every
    ``worker status`` invocation crash post-Commit-6 (the
    ``find_running_webui`` path now calls this eagerly). Use
    ``OpenProcess`` via ``ctypes`` and check ``GetExitCodeProcess``
    — STILL_ACTIVE means alive, anything else means terminated.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
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
    log = open(log_file, "a", buffering=1, encoding="utf-8")
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
                lines = log_file.read_text(encoding="utf-8").splitlines()[-20:]
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

    print(f"PID {pid} didn't exit after SIGTERM; force-killing.")
    # kill_process_tree handles both POSIX SIGKILL and Windows taskkill;
    # also takes out any uvicorn / channel-bot children the worker
    # spawned. signal.SIGKILL doesn't exist on Windows Python so we
    # can't do ``os.kill(pid, signal.SIGKILL)`` directly there.
    from openprogram._compat import kill_process_tree
    if not kill_process_tree(pid):
        clear_pid_file()
        clear_port_file()
        print("Stopped.")
        return 0

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
        raw = p.read_text(encoding="utf-8").strip().splitlines()
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
    port, pid, source = find_running_webui()

    if source == "none":
        print("openprogram worker: not running")
        print()
        print("  Start it with:  openprogram worker start")
        print("  Or install as a service:  openprogram worker install")
        return 0

    if source == "unmanaged":
        # Foreground ``--web`` or ``web`` — webui is up, but not under
        # ``worker start`` management, so ``worker stop`` / ``restart``
        # can't touch it. Be transparent about that.
        print(f"openprogram webui: running on :{port}  (unmanaged)")
        print()
        print("  Started via `openprogram web` —")
        print("  the foreground process owns it. `worker stop` will not")
        print("  affect this instance; Ctrl-C in that terminal will.")
        print()
        print("  For a managed worker, stop the foreground process and")
        print("  run:  openprogram worker start")
        return 0

    started = _worker_start_time(pid) if pid is not None else None
    age = ""
    if started is not None:
        age = f", up {_format_duration(time.time() - started)}"

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
