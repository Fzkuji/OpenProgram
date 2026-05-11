"""Foreground run-loop for the persistent worker.

The worker hosts:
  1. The webui WebSocket server (always — that's the point of the worker).
  2. Any configured channel adapters (Discord, Telegram, WeChat, ...) as
     daemon threads. Channels are optional; the worker is happy to run
     with zero channels.

Everything lives in a single asyncio loop / process so channel
broadcasts reach attached webui clients without cross-process plumbing.
"""
from __future__ import annotations

import signal as _signal
import socket
import threading
import time
from typing import Optional

from .lifecycle import (
    clear_pid_file,
    clear_port_file,
    write_pid_file,
    write_port_file,
)
from .lock import WorkerLock


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_available(port: int) -> bool:
    """True iff we can bind ``127.0.0.1:port`` right now.

    Sets ``SO_REUSEADDR`` before ``bind()`` so a port that only sits
    in ``TIME_WAIT`` (left by a worker we just stopped) is reported
    as available. Without this, every quick restart shifts the
    backend off ``8765`` to a random port for ~60s, which forces a
    Next.js bundle rebuild + makes every open browser tab lose its
    WebSocket. ``uvicorn`` also sets ``SO_REUSEADDR`` on its server
    socket, so the actual subsequent bind succeeds too.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _start_channel_threads() -> tuple[
    Optional[threading.Event],
    list[tuple[str, threading.Thread]],
]:
    """Spin up a daemon thread per (channel, account) that's enabled +
    configured + implemented. Returns (stop_event, threads).

    Returns (None, []) if no viable channel is configured. The worker
    keeps running with just the webui in that case.
    """
    try:
        from openprogram.channels import build_channel, list_status
    except ImportError:
        return None, []

    try:
        rows = list_status()
    except Exception:
        return None, []

    stop = threading.Event()
    threads: list[tuple[str, threading.Thread]] = []
    for row in rows:
        channel = row["platform"]
        account_id = row["account_id"]
        label = f"{channel}:{account_id}"
        if not row.get("enabled"):
            print(f"[{label}] disabled — skipped.")
            continue
        if not row.get("configured"):
            print(f"[{label}] credentials missing — skipped.")
            continue
        if not row.get("implemented"):
            print(f"[{label}] no implementation — skipped.")
            continue
        try:
            ch = build_channel(channel, account_id)
            if ch is None:
                continue
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] init failed: {type(e).__name__}: {e}")
            continue
        t = threading.Thread(
            target=_safe_run_channel,
            args=(label, ch, stop),
            daemon=True,
            name=f"channel-{channel}-{account_id}",
        )
        t.start()
        threads.append((label, t))

    if not threads:
        return None, []
    return stop, threads


def _safe_run_channel(label: str, channel, stop: threading.Event) -> None:
    try:
        channel.run(stop)
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"[{label}] crashed: {type(e).__name__}: {e}")
        print("".join(traceback.format_exception(type(e), e, e.__traceback__)))


def run_foreground() -> int:
    """Run the worker in the current process. Blocks until SIGTERM / Ctrl-C."""
    lock = WorkerLock()
    if not lock.try_acquire():
        holder = lock.holder_pid
        print(
            f"[worker] another worker is already running"
            + (f" (PID {holder})" if holder is not None else "")
            + ". Exiting."
        )
        return 1

    # Bring up the webui first — that's the worker's primary job.
    # Backend port is fixed (default 8765) so the bundled Next.js
    # frontend's rewrites compile against a stable target.
    import os
    from openprogram.webui import start_web

    port = int(os.environ.get("OPENPROGRAM_BACKEND_PORT", "8765"))
    if not _port_available(port):
        port = _find_free_port()
        print(
            f"[worker] backend port {os.environ.get('OPENPROGRAM_BACKEND_PORT', '8765')} taken; using free port {port}"
        )
    start_web(port=port, open_browser=False)
    write_port_file(port)
    write_pid_file()
    print(f"[worker] webui WS at ws://127.0.0.1:{port}/ws")

    # Warm the provider cache in a background thread so the first HTTP
    # request (e.g. /api/providers/list when the user opens /programs)
    # doesn't have to do the 3-5s probe itself.
    def _warm_providers() -> None:
        try:
            from openprogram.webui import _runtime_management as rm
            rm._init_providers()
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] provider warm-up failed: {exc}")

    threading.Thread(target=_warm_providers, daemon=True, name="provider-warmup").start()

    # Frontend (Next.js). Optional — falls back gracefully if node/npm
    # missing or OPENPROGRAM_NO_WEB is set.
    try:
        from .web import start_web_frontend, stop_web_frontend
        web_proc = start_web_frontend(backend_port=port)
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] web frontend failed to start: {exc}")
        web_proc = None
        def stop_web_frontend(_p):  # fallback noop
            return None

    stop_event, channel_threads = _start_channel_threads()
    if channel_threads:
        labels = ", ".join(label for label, _ in channel_threads)
        print(f"[worker] channels: {labels}")
    else:
        print("[worker] channels: none configured (worker still running)")

    # Fire-and-forget update check. Result lands in the staged-notice
    # file; the TUI reads it on next launch and shows a banner.
    try:
        from openprogram.updater import background_check_and_apply
        background_check_and_apply()
    except Exception:  # noqa: BLE001
        pass

    # Memory subsystem — daily sleep sweep + session-end watcher.
    try:
        from openprogram.memory.scheduler import start_in_worker as _start_sleep
        from openprogram.memory.session_watcher import start_in_worker as _start_watcher
        from openprogram.memory.llm_bridge import build_default_llm
        _llm = build_default_llm()
        _start_sleep(llm=_llm)
        _start_watcher()
        if _llm is not None:
            print("[worker] memory: sleep + session-end watcher running")
        else:
            print("[worker] memory: watcher running, no default LLM (sleep deep/REM will skip)")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] memory subsystem failed to start: {exc}")

    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt

    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass

    try:
        # Block forever — webui server runs on its own threads/loop, so
        # we just need to keep the main thread alive. If channels are
        # running, we also want to react when they all die (worker can
        # keep running with just webui though, so don't exit).
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[worker] stopping...")
        if stop_event is not None:
            stop_event.set()
        for label, t in channel_threads:
            t.join(timeout=3)
            if t.is_alive():
                print(f"[{label}] still running; drops on process exit")
        stop_web_frontend(web_proc)
    finally:
        lock.release()
        clear_pid_file()
        clear_port_file()
    return 0
