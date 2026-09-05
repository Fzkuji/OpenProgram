"""Lifecycle controls for the persistent worker process.

The worker hosts the webui WebSocket server even when no channel is
configured. Channel polling remains an optional add-on.

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
from collections.abc import Callable
from typing import Optional

from openprogram._compat import no_window_creation_flags

from . import paths
from .lock import is_held_by, read_holder_pid


# port file


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

    Also falls back to a TCP probe of the default port (18100) so a
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
    port = _default_webui_port()
    if _probe_tcp_listening(port):
        return port
    return None


def resolve_worker_port() -> int:
    """Single-port resolution (docs/reference/design/cli/single-port.md).

    Priority: ``OPENPROGRAM_WEB_PORT`` → UI pref ``web_port`` → 18100.

    Multi-instance setups (a stable and a dev worker side by side)
    differ only by profile + env.
    """
    raw = os.environ.get("OPENPROGRAM_WEB_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    try:
        from openprogram.setup import _read_config
        ui = _read_config().get("ui") or {}
        if ui.get("web_port"):
            return int(ui["web_port"])
    except Exception:
        pass
    return 18100


def _default_webui_port() -> int:
    """Default worker port for TCP probing — same resolution as startup."""
    return resolve_worker_port()


def _probe_tcp_listening(port: int, host: str = "127.0.0.1",
                         timeout_s: float = 0.4) -> bool:
    """Cheap TCP-connect probe. True if something accepted; False on
    refusal, timeout, or any other socket error. Thin alias over the
    shared :func:`openprogram._ports.port_in_use`."""
    from openprogram._ports import port_in_use
    return port_in_use(port, host=host, timeout=timeout_s)


def find_running_webui() -> tuple[Optional[int], Optional[int], str]:
    """Locate any webui the user has running. Returns (port, pid, source).

    Three states:

    - ``(port, pid, "managed")`` — ``worker.lock`` + ``worker.pid`` say
      a worker is alive; this is the well-supported path.
    - ``(18100, None, "unmanaged")`` — no lock/pid, but a process is
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
        if _probe_tcp_listening(_default_webui_port()):
            return _default_webui_port(), pid, "managed"
    if _probe_tcp_listening(_default_webui_port()):
        return _default_webui_port(), None, "unmanaged"
    return None, None, "none"


# pid file


def _read_pid_file() -> Optional[int]:
    p = paths.pid_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip().splitlines()
        pid = int(raw[0]) if raw else None
        return pid if pid is not None and pid > 0 else None
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
    from openprogram._compat import process_alive
    return process_alive(pid)


def current_worker_pid() -> Optional[int]:
    """Return the PID of the live worker, or None.

    The bytes in ``worker.lock`` are only a hint: a crashed process leaves
    them behind even though the kernel has released the advisory lock, and
    that PID may later be reused by an unrelated process.  Verify actual lock
    ownership before trusting the holder.  The legacy PID sidecar fallback is
    accepted only when its live process still has an OpenProgram worker
    command line, so ``openprogram stop`` never signals an unrelated reused
    PID.
    """
    holder = read_holder_pid()
    if (
        holder is not None
        and _process_alive(holder)
        and is_held_by(holder)
    ):
        return holder
    pid = _read_pid_file()
    if (
        pid is not None
        and _process_alive(pid)
        and _looks_like_worker_process(pid)
    ):
        return pid
    return None


def _looks_like_worker_process(pid: int) -> bool:
    """Best-effort identity check for the legacy PID-file fallback."""

    from openprogram._compat import process_command_line

    command = " ".join(process_command_line(pid).lower().split())
    return "openprogram" in command and "worker run" in command


# start / stop


def _detached_worker_command(flags=None) -> list[str]:
    """Preserve isolation and bytecode policy across the worker re-exec."""
    active_flags = sys.flags if flags is None else flags
    command = [sys.executable]
    if active_flags.isolated:
        command.append("-I")
    if active_flags.dont_write_bytecode:
        command.append("-B")
    command.extend(["-u", "-m", "openprogram", "worker", "run"])
    return command


def spawn_detached(
    *,
    prefer_service: bool = True,
    on_spawn: Callable[[int], None] | None = None,
) -> int:
    """Start the worker through its installed service or as a detached child.

    ``on_spawn`` runs immediately after ``Popen`` returns, before readiness is
    polled.  Release installers use it to durably record the detached session
    leader so cleanup can terminate the whole process group even before the
    worker has written its ordinary lock and PID files.
    """
    if prefer_service:
        from openprogram.worker import services

        service_result = services.start_if_installed()
        if service_result is not None:
            return service_result

    existing = current_worker_pid()
    if existing is not None:
        port = read_worker_port()
        port_str = f", port {port}" if port else ""
        print(
            f"openprogram already running (PID {existing}{port_str}). "
            f"Stop it first with `openprogram stop`."
        )
        return 1

    log_file = paths.log_path()
    # -u: unbuffered, so 'worker status' shows fresh output immediately.
    # --foreground: re-exec must not loop back through spawn_detached.
    cmd = _detached_worker_command()
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
            creationflags=no_window_creation_flags(),
            cwd=Path.home(),
        )
    except Exception as e:  # noqa: BLE001
        log.close()
        print(f"failed to spawn worker: {type(e).__name__}: {e}")
        return 1
    if on_spawn is not None:
        try:
            on_spawn(proc.pid)
        except Exception as e:  # noqa: BLE001
            # A caller that cannot record ownership must not lose a detached
            # worker.  Kill the just-created tree before returning failure;
            # this is especially important before worker.lock exists.
            from openprogram._compat import kill_process_tree

            kill_process_tree(proc.pid)
            log.close()
            print(f"failed to record spawned worker: {type(e).__name__}: {e}")
            return 1
    # Popen duplicated/inherited the descriptor for the child.  The CLI or TUI
    # parent must not retain its own copy for the rest of an interactive
    # session; doing so leaked one descriptor on every worker recovery.
    log.close()

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
            print(f"openprogram started (PID {proc.pid}{port_str}). Logs: {log_file}")
            return 0

    print(f"openprogram starting (PID {proc.pid}); not yet ready. Watch {log_file}.")
    return 0


def stop_worker(*, prefer_service: bool = True) -> int:
    """Stop the service-owned or detached worker, escalating when required."""
    if prefer_service:
        from openprogram.worker import services

        service_result = services.stop_if_installed()
        if service_result is not None:
            return service_result

    pid = current_worker_pid()
    if pid is None:
        print("openprogram: not running.")
        return 0
    print(f"Stopping openprogram (PID {pid})...")
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


def restart_worker(*, prefer_service: bool = True) -> int:
    """Refresh a managed service or restart the ordinary detached worker."""
    if prefer_service:
        from openprogram.worker import services

        service_result = services.restart_if_installed()
        if service_result is not None:
            return service_result

    if current_worker_pid() is not None:
        rc = stop_worker(prefer_service=False)
        if rc != 0:
            return rc
        # Wait a beat for the lock + port files to clear.
        deadline = time.time() + 2.0
        while time.time() < deadline and current_worker_pid() is not None:
            time.sleep(0.1)
    return spawn_detached(prefer_service=False)


# status


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
        print("openprogram: not running")
        print()
        print("  Start it with:  openprogram")
        print("  Or install as a service (auto-start at login):  openprogram worker install")
        return 0

    if source == "unmanaged":
        # Foreground ``--web`` or ``web`` — webui is up, but not under
        # the managed background service, so ``openprogram stop`` /
        # ``restart`` can't touch it. Be transparent about that.
        print(f"openprogram: running on :{port}  (foreground `openprogram web`)")
        print()
        print("  This instance is owned by the terminal that launched")
        print("  `openprogram web`; `openprogram stop` won't affect it —")
        print("  Ctrl-C in that terminal will.")
        print()
        print("  For a background service that survives closing the terminal,")
        print("  stop the foreground process and run:  openprogram")
        return 0

    started = _worker_start_time(pid) if pid is not None else None
    age = ""
    if started is not None:
        age = f", up {_format_duration(time.time() - started)}"

    port_str = f", port {port}" if port else ""
    print(f"openprogram: running (PID {pid}{port_str}{age})")
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
