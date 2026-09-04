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
    worker off its fixed port to a random port for ~60s, which forces a
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
    # Health watcher: stamps a heartbeat for each (channel, account_id)
    # whose adapter thread is still alive. The webui status endpoint
    # reads these to surface a live/dead signal in the UI. Coarse
    # (5s resolution) but accurate for "thread crashed vs running".
    _start_heartbeat_watcher(threads, stop)
    return stop, threads


def _start_heartbeat_watcher(
    threads: list[tuple[str, threading.Thread]],
    stop: threading.Event,
) -> threading.Thread:
    from openprogram.channels._heartbeats import heartbeat, clear

    # Parse "channel:account_id" labels once so the hot loop just does
    # is_alive() + dict writes — no string ops per tick.
    parsed = []
    for label, t in threads:
        ch, _, acct = label.partition(":")
        parsed.append((ch, acct or "default", t))
        # Prime each entry so a status query right after startup
        # (before the first 5s tick lands) already sees a heartbeat.
        heartbeat(ch, acct or "default")

    def _loop() -> None:
        while not stop.is_set():
            for ch, acct, t in parsed:
                if t.is_alive():
                    heartbeat(ch, acct)
                else:
                    clear(ch, acct)
            stop.wait(5.0)

    watcher = threading.Thread(
        target=_loop, name="channel-heartbeat-watcher", daemon=True
    )
    watcher.start()
    return watcher


def _safe_run_channel(label: str, channel, stop: threading.Event) -> None:
    try:
        # run_forever = 崩溃自动退避重连 (base.Channel); 正常 return 表示
        # adapter 永久停止 (凭据失效等), 不再重启.
        channel.run_forever(stop)
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

    from openprogram.providers.initialization import initialize_provider_runtime

    try:
        initialize_provider_runtime()
    except BaseException:
        lock.release()
        raise

    # Functions execute in isolated child processes. Those children may
    # durably enqueue agent jobs, but only this singleton worker owns the
    # profile lock required to claim and execute them. Keep the dispatcher
    # alive even when no WebSocket job action has initialized it yet.
    from openprogram.agent.job import get_runner as get_job_runner
    from openprogram.agent.job.runner import shutdown_runner as shutdown_job_runner

    get_job_runner()
    print("[worker] job dispatcher: running")

    # Bring up the webui first — that's the worker's primary job. Single
    # port: this process serves the API, /ws AND the frontend export.
    import os
    from openprogram.webui import start_web
    from .lifecycle import resolve_worker_port

    fixed_port = resolve_worker_port()
    port = fixed_port
    if not _port_available(port):
        # The fixed port is genuinely held by another live listener
        # (SO_REUSEADDR already lets us reclaim a TIME_WAIT port, so this
        # isn't a recent self-restart). Name who holds it before falling
        # back — a silent drift to a random port hides the real cause and
        # breaks the fixed-port URL. (openclaw's describePortOwner.)
        from openprogram._ports import describe_port_owner
        owner = describe_port_owner(fixed_port)
        port = _find_free_port()
        if owner is not None:
            who = "another openprogram instance" if owner.is_ours else "a foreign process"
            print(f"[worker] port {fixed_port} is held by {who} — {owner.detail}")
            if not owner.is_ours:
                print(f"[worker]   free it (`lsof -ti:{fixed_port} | xargs kill`) "
                      f"or set OPENPROGRAM_WEB_PORT to keep the fixed port.")
        print(f"[worker] falling back to free port {port} (UI URL will track this port).")
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

    # Frontend build gate — the static export (apps/web/out/) is served by
    # this same process; just make sure it's fresh. Synchronous: the UI
    # isn't usable before it exists anyway. Failure is non-fatal (the
    # API/TUI still work; / returns 503 with a hint).
    if os.environ.get("OPENPROGRAM_NO_WEB", "").strip() not in ("1", "true", "yes"):
        try:
            from openprogram.webui.frontend import ensure_frontend_built
            print("[worker] web: checking frontend export…")
            ensure_frontend_built()
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] web: frontend build failed: {exc}")

    recovery_ready = False
    try:
        from openprogram.self_update.recovery import recover_pending_updates
        recovery_ready = recover_pending_updates()
    except Exception as exc:
        print(f"[worker] self-update recovery failed: {type(exc).__name__}")

    stop_event, channel_threads = _start_channel_threads()
    if channel_threads:
        labels = ", ".join(label for label, _ in channel_threads)
        print(f"[worker] channels: {labels}")
    else:
        print("[worker] channels: none configured (worker still running)")

    scheduler_stop = None
    scheduler_thread = None
    try:
        from openprogram.programs.tools.jobs.cron.worker import start_in_worker

        if recovery_ready:
            scheduler_stop, scheduler_thread = start_in_worker()
            print("[worker] scheduler: running")
        else:
            print("[worker] scheduler: deferred by self-update recovery failure")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] scheduler failed to start: {exc}")

    # Memory subsystem — nightly reorganisation + session-end writer.
    try:
        from openprogram.memory.scheduler import start_nightly_reorganizer
        from openprogram.memory.session_watcher import start_idle_session_watcher
        start_nightly_reorganizer()
        start_idle_session_watcher()
        print("[worker] memory: nightly reorganisation + session-end writer running")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] memory subsystem failed to start: {exc}")

    # 事件层 B 类桥 — auth 的 subscribe/_emit 信号翻译进统一总线
    # (docs/design/proactive/event-layer.md §3)。
    try:
        from openprogram.events.bridges import install_event_bridges
        if install_event_bridges():
            print("[worker] event bridges installed (auth → bus)")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] event bridges failed to install: {exc}")

    # 用户 shell 订阅者 — config.json 顶层 "hooks" 注册到总线（gate 型
    # 事件挂同步闸门，notify 型挂后台命令）。改配置重启生效。
    try:
        from openprogram.events import install_config_hooks
        _n_hooks = install_config_hooks()
        if _n_hooks:
            print(f"[worker] {_n_hooks} config hook subscriber(s) registered")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] config hooks failed to install: {exc}")

    # Channel question bridge — push runtime.ask/form questions from a
    # channel session into that chat so the user can /answer them.
    try:
        from openprogram.channels._question_bridge import install_question_bridge
        install_question_bridge()
        print("[worker] channel question bridge installed")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] channel question bridge failed to install: {exc}")

    # Programs watcher — auto-detect agentic harnesses installed at
    # runtime (clone into agentics/ or `programs install`) so their
    # functions go live + the UI refreshes without a restart.
    try:
        from openprogram.programs.watcher import start_in_worker as _start_programs_watch
        if _start_programs_watch() is not None:
            print("[worker] programs watcher running (auto-detect installs)")
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] programs watcher failed to start: {exc}")

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
        if scheduler_stop is not None:
            scheduler_stop.set()
        # Shared join budget, not 3s per thread: `openprogram stop` waits
        # 5s after SIGTERM before force-killing, and two channel bots at
        # 3s each already blew that window — every stop ended in SIGKILL.
        # Threads that don't stop in time drop on process exit anyway.
        _join_deadline = time.time() + 2.0
        for label, t in channel_threads:
            t.join(timeout=max(0.1, _join_deadline - time.time()))
            if t.is_alive():
                print(f"[{label}] still running; drops on process exit")
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=max(0.1, _join_deadline - time.time()))
    finally:
        shutdown_job_runner()
        lock.release()
        clear_pid_file()
        clear_port_file()
    return 0
