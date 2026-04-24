"""Background-worker controls for ``openprogram channels start --detach``.

Design: channel polling lives in its own long-running process, not
inside the CLI chat or the Web UI. Reason — those are front-ends
(REPL, browser tabs) and should be spawnable / closeable at will.
Binding channel lifetime to any one of them means closing your chat
window kills the WeChat bot.

We reuse fcntl.flock via ``ChannelsLock``: only one worker can run
at a time, and ``read_holder_pid`` lets us answer "is it alive?"
from outside the worker.

Layout:
  <state>/channels.lock   — fcntl-locked PID file (written by ChannelsLock)
  <state>/channels.pid    — same PID, written by the worker on startup
                             and cleaned on exit. Independent of the
                             fcntl lock so `status` / `stop` work even
                             if the flock file is somehow busy.
  <state>/channels.log    — combined stdout + stderr

Start / stop semantics:
  start --detach        — fork, child execs `openprogram channels start`,
                          parent prints the new PID and returns.
  stop                  — SIGTERM the PID from channels.pid (or lock
                          file as fallback); waits up to 5s then reports.
  status                — queries the PID file + kill(pid, 0) liveness.

Naming: we call the background process a "worker" to line up with
``openprogram cron-worker``. The underlying POSIX concept is a
daemon, but user-facing text says worker everywhere for consistency.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _state_paths() -> tuple[Path, Path]:
    """Return (pid_file, log_file) paths under the active state dir."""
    from openprogram.paths import get_state_dir
    root = get_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / "channels.pid", root / "channels.log"


def _read_pid_file() -> Optional[int]:
    pid_file, _ = _state_paths()
    if not pid_file.exists():
        return None
    try:
        raw = pid_file.read_text().strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    """Is a PID a live process? Uses kill(pid, 0) — raises if it's
    not ours to signal, which we treat as "running but owned by
    another user"; safer to answer "alive" than silently re-spawn.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def current_worker_pid() -> Optional[int]:
    """Return the PID of a live channels worker, or None.

    Prefers the lock file (fcntl-backed, authoritative for "someone
    is actively holding channels"); falls back to the .pid sidecar
    if the lock file was cleared by a clean release but the worker
    kept running (shouldn't happen but be defensive).
    """
    from openprogram.channels._lock import read_holder_pid
    holder = read_holder_pid()
    if holder is not None and _process_alive(holder):
        return holder
    pid = _read_pid_file()
    if pid is not None and _process_alive(pid):
        return pid
    return None


def write_pid_file() -> None:
    """Called by the worker on startup to record its PID.

    Also writes a stamp line with start time + python path so
    ``status`` can show useful context.
    """
    pid_file, _ = _state_paths()
    pid_file.write_text(f"{os.getpid()}\n{int(time.time())}\n")


def clear_pid_file() -> None:
    """Called by the worker on clean exit."""
    pid_file, _ = _state_paths()
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def spawn_detached() -> int:
    """Fork a background worker running ``openprogram channels start``.

    Uses Popen with ``start_new_session=True`` so the child becomes
    its own session leader — it won't die when the parent terminal
    closes. stdout + stderr redirect to ``channels.log``; the child
    picks up writing the pid file on startup.

    Returns an exit code (0 on success, 1 if another worker is already
    running).
    """
    existing = current_worker_pid()
    if existing is not None:
        print(f"channels worker already running (PID {existing}). "
              f"Stop it first with `openprogram channels stop`.")
        return 1

    _, log_file = _state_paths()
    # Use the same python that's running us so the worker doesn't hit
    # a different environment / virtualenv.
    cmd = [sys.executable, "-m", "openprogram", "channels", "start"]
    log = open(log_file, "a", buffering=1)  # line-buffered
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

    # Give the child a moment to either acquire the lock or die. This
    # matters because --detach is advertised as "returns after the
    # worker is running" — a silent exit here would be a surprise.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        time.sleep(0.2)
        rc = proc.poll()
        if rc is not None:
            print(f"worker exited immediately (rc={rc}). "
                  f"Tail of {log_file}:")
            try:
                lines = log_file.read_text().splitlines()[-20:]
                for line in lines:
                    print(f"  {line}")
            except OSError:
                pass
            return 1
        if current_worker_pid() == proc.pid:
            active = _active_platform_list()
            if active:
                print(f"channels worker started (PID {proc.pid}), "
                      f"polling: {active}. Logs: {log_file}")
            else:
                print(f"channels worker started (PID {proc.pid}) but "
                      f"nothing enabled/configured to poll. "
                      f"Logs: {log_file}")
            return 0

    # Hit the timeout but child is still alive — lock hasn't landed
    # yet (slow network? slow init?). Report the PID and move on.
    print(f"channels worker starting (PID {proc.pid}); not yet ready. "
          f"Watch {log_file}.")
    return 0


def _active_platform_list() -> str:
    """Comma-joined ``channel:account`` labels this worker will poll.

    Filters by enabled + configured + implemented — same predicate
    the runner uses. Returns "" when nothing viable.
    """
    try:
        from openprogram.channels import list_status
        rows = list_status()
        labels = [
            f"{r['platform']}:{r['account_id']}"
            for r in rows
            if r.get("enabled") and r.get("implemented")
            and r.get("configured")
        ]
        return ", ".join(labels)
    except Exception:
        return ""


def stop_worker() -> int:
    """SIGTERM whichever process is holding the channels lock."""
    pid = current_worker_pid()
    if pid is None:
        print("No channels worker running.")
        return 0
    print(f"Stopping channels worker (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("Process already gone.")
        clear_pid_file()
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
    # Still alive — escalate.
    print(f"PID {pid} didn't exit after SIGTERM; sending SIGKILL.")
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return 0


def print_status() -> int:
    """Print a one-line report on the channels worker."""
    pid = current_worker_pid()
    if pid is None:
        print("channels worker: not running")
        return 0
    started = _worker_start_time(pid)
    age = ""
    if started is not None:
        age = f", up {_format_duration(time.time() - started)}"
    _, log_file = _state_paths()
    print(f"channels worker: running (PID {pid}{age})")
    print(f"  logs: {log_file}")
    try:
        from openprogram.channels import list_status
        rows = list_status()
        active = [r for r in rows
                  if r.get("enabled") and r.get("implemented")
                  and r.get("configured")]
        if active:
            labels = [f"{r['platform']}:{r['account_id']}" for r in active]
            print(f"  active: {', '.join(labels)}")
    except Exception:
        pass
    return 0


def _worker_start_time(pid: int) -> Optional[float]:
    """Read the start timestamp written by ``write_pid_file``.

    If the pid file's PID matches the process asking, second line is
    the unix ts. Otherwise we can't easily get it (no /proc on
    macOS) so return None.
    """
    pid_file, _ = _state_paths()
    try:
        raw = pid_file.read_text().strip().splitlines()
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


def prompt_spawn_if_configured_but_dead(
    console,
    *,
    verb: str,
) -> Optional[int]:
    """Offer to spawn the worker on a front-end's first run.

    If channels are configured AND no worker is currently live,
    prints a one-question prompt asking whether to fork one now.
    ``verb`` is a short label shown in the prompt ("chat" / "web UI")
    so the reason is obvious.

    Returns the worker PID if one is now running (either pre-existing
    or newly spawned), or None if the user declined.
    """
    try:
        from openprogram.channels import list_channels_status
    except Exception:
        return None
    try:
        rows = list_channels_status()
    except Exception:
        return None
    viable = [r for r in rows
              if r.get("enabled") and r.get("implemented")
              and r.get("configured")]
    if not viable:
        return None

    pid = current_worker_pid()
    if pid is not None:
        names = ", ".join(r["platform"] for r in viable)
        console.print(
            f"[dim]↪ channels worker running (PID {pid}): {names}  "
            f"(stop with `openprogram channels stop`)[/]"
        )
        return pid

    names = ", ".join(r["platform"] for r in viable)
    console.print()
    console.print(
        f"[yellow]Chat channels configured ({names}) but no worker running."
        f"[/]"
    )
    # Delegate to setup._confirm so the arrow-key prompt style
    # matches the rest of the flow. Local import breaks the cycle.
    from openprogram.setup import _confirm
    if not _confirm(
        f"Start the channels worker now in the background so the bots "
        f"receive messages while you {verb}?",
        default=True,
    ):
        console.print(
            "[dim]Skipped. Run `openprogram channels start --detach` "
            "when you want the bots online.[/]"
        )
        return None

    rc = spawn_detached()
    if rc != 0:
        return None
    return current_worker_pid()
