"""Runner for chat-channel bots.

Two entry points:

    run_all()  —  blocking. Used by `openprogram channels start` and
                   the detached worker path. Starts every
                   enabled+configured channel in a background thread,
                   waits for Ctrl-C / SIGTERM, then shuts them down.

    start_all() — non-blocking. Kept for callers that want to co-host
                   channels inside another long-running process.
                   Returns (stop_event, threads, lock); caller drives
                   shutdown.

A process-wide exclusive fcntl lock (``ChannelsLock``) gates both: at
most one process pulls channel updates at a time. Multiple
``openprogram`` windows is a normal workflow (different conversations
in different terminals) — without the lock they'd race to
``getupdates`` and answer the same user message N times from N
unpredictable sessions.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Optional

from openprogram.channels import (
    build_channel,
    list_channels_status,
    list_enabled_platforms,
)

if TYPE_CHECKING:
    from openprogram.channels._lock import ChannelsLock


def start_all(*, quiet: bool = False) -> tuple[Optional[threading.Event],
                                                list[tuple[str, threading.Thread]],
                                                Optional["ChannelsLock"]]:
    """Kick off every enabled+configured channel in a daemon thread.

    Returns ``(stop_event, threads, lock)``:
      * ``stop_event`` — set this to stop the threads. None iff we
        couldn't acquire the channels lock (another process already
        owns it).
      * ``threads`` — [(platform_id, Thread), ...]. Empty if we didn't
        start anything.
      * ``lock`` — the ``ChannelsLock`` we hold. Call ``lock.release()``
        after joining threads. None iff we didn't acquire it.

    Only one process at a time can own channels (fcntl flock on
    ``<state>/channels.lock``). This stops multiple `openprogram`
    windows from racing to pull the same Telegram / WeChat updates.

    ``quiet=True`` suppresses the "[pid] enabled but token missing"
    warnings so the CLI chat doesn't dump noise on every launch.
    """
    from openprogram.channels._lock import ChannelsLock

    lock = ChannelsLock()
    if not lock.try_acquire():
        if not quiet:
            print(f"[channels] another process (PID {lock.holder_pid}) "
                  f"already owns channels; skipping here.")
        return None, [], None

    status = list_channels_status()
    stop = threading.Event()
    threads: list[tuple[str, threading.Thread]] = []

    for row in status:
        if not row.get("enabled"):
            continue
        pid = row["platform"]
        if not row.get("implemented"):
            if not quiet:
                print(f"[{pid}] runtime not implemented yet; skipped.")
            continue
        if not row.get("configured"):
            if not quiet:
                print(f"[{pid}] enabled but token missing "
                      f"(${row.get('env')}); skipped.")
            continue
        try:
            ch = build_channel(pid)
            if ch is None:
                continue
        except Exception as e:  # noqa: BLE001
            print(f"[{pid}] init failed: {type(e).__name__}: {e}")
            continue
        t = threading.Thread(target=_safe_run, args=(pid, ch, stop),
                             daemon=True, name=f"channel-{pid}")
        t.start()
        threads.append((pid, t))

    if not threads:
        # Got the lock but nothing to run — release so another process
        # configuring a channel can pick up the slack.
        lock.release()
        return None, [], None

    return stop, threads, lock


def run_all() -> int:
    """Blocking — start every enabled channel and wait for Ctrl-C or
    SIGTERM.

    Entry point for ``openprogram channels start`` (both foreground
    and the detached worker path, since the detached form simply
    execs us with stdout piped to a log file). Writes a PID file
    on startup and clears it on exit so ``openprogram channels
    status`` / ``stop`` can find us.
    """
    from openprogram.channels.worker import write_pid_file, clear_pid_file

    status = list_channels_status()
    enabled = [r["platform"] for r in status if r["enabled"]]
    if not enabled:
        print("No channels enabled. Configure with "
              "`openprogram config channels`.")
        return 1

    stop, threads, lock = start_all(quiet=False)

    if not threads or stop is None or lock is None:
        # start_all already printed the "owned by PID N" or
        # "no channel started" explanation.
        return 1

    write_pid_file()

    # SIGTERM → same clean-shutdown path as Ctrl-C so the stop command
    # from openprogram.channels.worker.stop_worker works.
    import signal as _signal
    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt
    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        # Not main thread or platform doesn't allow — skip silently.
        pass

    try:
        while any(t.is_alive() for _, t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[runner] stopping channels...")
        stop.set()
        for pid, t in threads:
            t.join(timeout=3)
            if t.is_alive():
                print(f"[{pid}] still running; it'll drop on process exit")
    finally:
        lock.release()
        clear_pid_file()
    return 0


def _safe_run(pid: str, channel, stop: threading.Event) -> None:
    try:
        channel.run(stop)
    except Exception as e:  # noqa: BLE001
        import traceback
        # Short form first so it's visible above any REPL noise, then
        # the full traceback so the user (or us, reading their logs)
        # can actually fix whatever broke. A one-liner alone led to
        # silent-ish failures where channels never polled and nobody
        # knew why.
        print(f"[{pid}] crashed: {type(e).__name__}: {e}")
        print("".join(traceback.format_exception(type(e), e, e.__traceback__)))
