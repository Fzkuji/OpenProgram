"""Channel worker runner.

For every (channel, account) that's enabled + configured, spin up a
daemon thread running ``Channel.run(stop)``. One account = one thread;
lifecycle is shared across all of them (Ctrl-C / SIGTERM stops
everyone, cleanly joins, releases the lock file).

Entry points:

    run_all()    — blocking (``openprogram channels start --fg`` and
                   the detached worker). Acquires the lock, writes a
                   PID file, installs a SIGTERM handler.

    start_all()  — non-blocking variant. Returns (stop, threads, lock)
                   for co-hosting inside another long-running process
                   (not currently used, kept for future webui embed).
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Optional

from openprogram.channels import build_channel, list_status

if TYPE_CHECKING:
    from openprogram.channels._lock import ChannelsLock


def start_all(*, quiet: bool = False) -> tuple[
    Optional[threading.Event],
    list[tuple[str, threading.Thread]],
    Optional["ChannelsLock"],
]:
    """Kick off one daemon thread per viable (channel, account).

    Returns ``(stop_event, threads, lock)``. Threads are named
    ``channel-<channel>-<account_id>`` for easy log attribution.

    Only one process at a time can own channels (fcntl flock on
    ``<state>/channels.lock``).
    """
    from openprogram.channels._lock import ChannelsLock

    lock = ChannelsLock()
    if not lock.try_acquire():
        if not quiet:
            print(f"[channels] another process (PID {lock.holder_pid}) "
                  f"already owns channels; skipping here.")
        return None, [], None

    rows = list_status()
    stop = threading.Event()
    threads: list[tuple[str, threading.Thread]] = []

    for row in rows:
        channel = row["platform"]
        account_id = row["account_id"]
        label = f"{channel}:{account_id}"
        if not row.get("enabled"):
            if not quiet:
                print(f"[{label}] disabled — skipped.")
            continue
        if not row.get("configured"):
            if not quiet:
                print(f"[{label}] credentials missing — skipped.")
            continue
        try:
            ch = build_channel(channel, account_id)
            if ch is None:
                continue
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] init failed: {type(e).__name__}: {e}")
            continue
        t = threading.Thread(
            target=_safe_run,
            args=(label, ch, stop),
            daemon=True,
            name=f"channel-{channel}-{account_id}",
        )
        t.start()
        threads.append((label, t))

    if not threads:
        lock.release()
        return None, [], None

    return stop, threads, lock


def run_all() -> int:
    """Blocking — spin up every viable channel-account thread and
    wait for Ctrl-C or SIGTERM."""
    from openprogram.channels.worker import write_pid_file, clear_pid_file

    rows = list_status()
    if not rows:
        print("No channel accounts configured. Run "
              "`openprogram channels accounts add <channel>`.")
        return 1

    stop, threads, lock = start_all(quiet=False)
    if not threads or stop is None or lock is None:
        return 1

    write_pid_file()

    import signal as _signal
    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt
    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass

    try:
        while any(t.is_alive() for _, t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[runner] stopping channels...")
        stop.set()
        for label, t in threads:
            t.join(timeout=3)
            if t.is_alive():
                print(f"[{label}] still running; drops on process exit")
    finally:
        lock.release()
        clear_pid_file()
    return 0


def _safe_run(label: str, channel, stop: threading.Event) -> None:
    try:
        channel.run(stop)
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"[{label}] crashed: {type(e).__name__}: {e}")
        print("".join(traceback.format_exception(type(e), e, e.__traceback__)))
