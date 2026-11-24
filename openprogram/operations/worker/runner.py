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

import os
import signal as _signal
import sys
import threading
import time

from .lifecycle import (
    clear_pid_file,
    clear_port_file,
    write_pid_file,
    write_port_file,
)
from .lock import WorkerLock
from .startup_log import worker_print


def _settle_web_port() -> int | None:
    """The port this worker will listen on, or None when it cannot have one.

    Runs right after the lock is taken, so this worker is the profile's only
    one and a holder of the port is somebody else: another program, or
    another account's worker. A port the profile chose moves to the next free
    one (the App follows the record); a port the user set is refused with the
    holder named, never swapped silently for another.
    """
    from .lifecycle import claim_worker_port
    from .ports import describe_port_owner, port_bindable

    try:
        port, setting = claim_worker_port()
        if port_bindable(port):
            return port
        owner = describe_port_owner(port)
        holder = owner.detail if owner is not None else "another program or account"
        if setting is not None:
            worker_print(
                f"port {port} (set by {setting}) is in use by {holder}. "
                f"Exiting: quit that program or set another port."
            )
            return None
        moved, _ = claim_worker_port(held=port)
    except OSError as exc:
        worker_print(f"no port to listen on: {exc}. Exiting.")
        return None
    worker_print(f"port {port} is in use by {holder}; this profile now uses port {moved}.")
    return moved


class _ChannelSupervisor:
    """Keeps one adapter thread per enabled, configured (channel, account).

    Accounts change while the worker runs: an account linked after start
    (``openprogram channels setup``, the Web UI's QR login) must start
    receiving without a worker restart, a re-login replaces a stopped
    adapter, and a disabled or removed account stops. ``sync`` reconciles
    the running set with the account files; a watcher thread calls it when
    they change and keeps the running marks the status endpoint reads.

    It wakes on the ``channels`` topic (an account written in this process,
    an adapter that stopped, ended or is reconnecting) and on file events
    under ``<state>/channels`` for ``account.json`` and ``credentials.json``
    (:data:`FILES_TOPIC`: the CLI's ``openprogram channels setup``). It
    looks again every :data:`RESYNC_S` in case an event was lost, every
    :data:`POLL_S` where file events cannot be had, and retries an adapter
    that failed to start after :data:`RETRY_S`, doubling up to the resync.
    An adapter thread says when it ends (:func:`_safe_run_channel`), so a
    running adapter's mark is set once when it starts and cleared when it
    ends; nothing wakes on a timer to look at the threads.
    """

    FILES_TOPIC = "channels.files"
    POLL_S = 5.0
    RESYNC_S = 300.0
    RETRY_S = 5.0

    def __init__(self) -> None:
        self.stop = threading.Event()
        self._lock = threading.Lock()
        # label -> (thread, its own stop event, credentials stamp)
        self._running: dict[str, tuple[threading.Thread, threading.Event, float]] = {}
        self._adapters: dict[str, object] = {}
        self._reported: dict[str, str] = {}
        #: Whether the last sync could not build an adapter it should run.
        self.init_failed = False
        self._listener = None
        #: Labels whose running mark is set.
        self._marked: set[str] = set()
        #: label -> set by its adapter thread as it ends.
        self._ended: dict[str, threading.Event] = {}

    def threads(self) -> list[tuple[str, threading.Thread]]:
        with self._lock:
            return [(label, entry[0]) for label, entry in self._running.items()]

    def threads_holding_work(self) -> list[tuple[str, threading.Thread]]:
        """The adapter threads a stopping worker waits for.

        One inside a long poll holds nothing: it ends with the process, and
        what the platform would have answered is handed out after the restart.
        """
        with self._lock:
            return [(label, entry[0]) for label, entry in self._running.items()
                    if not getattr(self._adapters.get(label), "awaiting_platform", False)]

    def _report(self, label: str, message: str) -> None:
        # Say each skip once, not every sync.
        if self._reported.get(label) != message:
            self._reported[label] = message
            print(f"[{label}] {message}")

    def sync(self) -> list[str]:
        """Start what should run and stop what shouldn't; return started labels."""
        try:
            from openprogram.interfaces.channels import _heartbeats
            from openprogram.interfaces.channels import accounts as _accounts
            from openprogram.interfaces.channels import build_channel, list_status
            rows = list_status()
        except Exception:  # noqa: BLE001
            self.init_failed = True
            return []
        self.init_failed = False
        started: list[str] = []
        wanted: set[str] = set()
        with self._lock:
            for row in rows:
                channel = row["platform"]
                account_id = row["account_id"]
                label = f"{channel}:{account_id}"
                if not row.get("enabled"):
                    self._report(label, "disabled — skipped.")
                    continue
                if not row.get("configured"):
                    self._report(label, "credentials missing — skipped.")
                    continue
                if not row.get("implemented"):
                    self._report(label, "no implementation — skipped.")
                    continue
                wanted.add(label)
                try:
                    stamp = _accounts.account_credentials_path(channel, account_id).stat().st_mtime
                except OSError:
                    stamp = 0.0
                current = self._running.get(label)
                if current is not None:
                    thread, _own_stop, old_stamp = current
                    # A stopped adapter (expired login) restarts only after
                    # new credentials arrive, not in a loop.
                    if self._live(label, thread) or stamp == old_stamp:
                        continue
                try:
                    ch = build_channel(channel, account_id)
                    if ch is None:
                        continue
                except Exception as e:  # noqa: BLE001
                    self._report(label, f"init failed: {type(e).__name__}: {e}")
                    self.init_failed = True
                    _heartbeats.record_stop(channel, account_id, _heartbeats.STOP_FAILED)
                    continue
                own_stop = threading.Event()
                ended = threading.Event()
                thread = threading.Thread(
                    target=_safe_run_channel,
                    args=(label, ch, own_stop, ended),
                    daemon=True,
                    name=f"channel-{channel}-{account_id}",
                )
                _heartbeats.clear_stop(channel, account_id)
                thread.start()
                self._running[label] = (thread, own_stop, stamp)
                self._ended[label] = ended
                self._adapters[label] = ch
                self._reported.pop(label, None)
                started.append(label)
            for label in [k for k in self._running if k not in wanted]:
                _thread, own_stop, _stamp = self._running.pop(label)
                self._ended.pop(label, None)
                self._adapters.pop(label, None)
                own_stop.set()
                print(f"[{label}] stopped — account disabled or removed.")
        return started

    def shutdown(self) -> None:
        self.stop.set()
        if self._listener is not None:
            self._listener.wake()
        with self._lock:
            for _thread, own_stop, _stamp in self._running.values():
                own_stop.set()

    def _live(self, label: str, thread: threading.Thread) -> bool:
        ended = self._ended.get(label)
        return thread.is_alive() and not (ended is not None and ended.is_set())

    def mark_running(self) -> None:
        """Set the running mark of each live adapter once and clear the
        mark of one whose thread ended or that no longer runs."""
        from openprogram.interfaces.channels._heartbeats import clear, heartbeat

        live = {label for label, thread in self.threads() if self._live(label, thread)}
        with self._lock:
            marked = self._marked
            self._marked = set(live)
        for label in live - marked:
            channel, _, account = label.partition(":")
            heartbeat(channel, account or "default")
        for label in marked - live:
            channel, _, account = label.partition(":")
            clear(channel, account or "default")

    def start_watcher(self) -> threading.Thread:
        from openprogram.host._platform import changes
        from openprogram.state.paths import get_state_dir

        changes.watch(get_state_dir() / "channels", self.FILES_TOPIC, recursive=True,
                      names={"account.json", "credentials.json"})
        self._listener = listener = changes.listen(self.FILES_TOPIC)
        watcher: threading.Thread | None = None

        def on_channels(_keys: tuple[str, ...]) -> None:
            # Not for the marks this loop sets itself.
            if threading.current_thread() is not watcher:
                listener.wake()

        def _loop() -> None:
            retry = self.RETRY_S
            unsubscribe = changes.subscribe("channels", on_channels)
            try:
                while not self.stop.is_set():
                    due = time.time() + retry if self.init_failed else None
                    changes.wait_for(listener, resync=self.RESYNC_S, poll=self.POLL_S, due=due)
                    if self.stop.is_set():
                        break
                    for label in self.sync():
                        worker_print(f"channels: started {label}")
                    retry = min(retry * 2, self.RESYNC_S) if self.init_failed else self.RETRY_S
                    self.mark_running()
            finally:
                unsubscribe()

        # Mark the adapters the first sync started, so a status query right
        # after startup already sees them.
        self.mark_running()
        watcher = threading.Thread(
            target=_loop, name="channel-supervisor", daemon=True,
        )
        watcher.start()
        return watcher


def _safe_run_channel(label: str, channel, stop: threading.Event,
                      ended: threading.Event | None = None) -> None:
    from openprogram.interfaces.channels import _heartbeats

    platform, _, account_id = label.partition(":")
    try:
        # run_forever = 崩溃自动退避重连 (base.Channel); 正常 return 表示
        # adapter 永久停止 (凭据失效等), 不再重启.
        channel.run_forever(stop)
        if not stop.is_set():
            _heartbeats.record_stop(platform, account_id, _heartbeats.STOP_SIGNED_OUT)
    except Exception as e:  # noqa: BLE001
        _heartbeats.record_stop(platform, account_id, _heartbeats.STOP_FAILED)
        import traceback
        print(f"[{label}] crashed: {type(e).__name__}: {e}")
        print("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    finally:
        # The thread is ending: the supervisor clears its running mark now
        # instead of finding the dead thread on a timer.
        if ended is not None:
            ended.set()
        from openprogram.host._platform import changes
        changes.signal("channels", label)


def _raise_open_file_limit() -> None:
    """Lift the soft open-file limit toward the hard one.

    launchd starts the worker with a soft limit of 256. Directory size
    scans, the file tree, sockets and session stores share that budget,
    and running out makes every later open fail (projects.json included).
    macOS rejects a soft limit above OPEN_MAX (10240) even when the hard
    limit is unlimited.
    """
    try:
        import resource
    except ImportError:  # Windows
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
    if soft == resource.RLIM_INFINITY or soft >= target:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError) as exc:
        worker_print(f"open-file limit stays at {soft}: {exc}")


_STABLE_SERVICE_SECONDS = 600
_HEALTHY_START_POLLS = 60


def _clear_boot_attempts() -> None:
    """Forget this version's start records, so they count abnormal ends only."""
    from openprogram.operations.self_update.versions import layout, store

    runtime = layout.current_runtime()
    if runtime is not None:
        store.clear_attempts(runtime.layout, runtime.version)


def _clear_boot_attempts_while_serving() -> None:
    """A version that has served for 10 minutes has not failed to start, and it goes on not failing."""
    from openprogram.operations.self_update.versions import layout

    if not layout.managed():
        return

    def clear_periodically() -> None:
        while True:
            time.sleep(_STABLE_SERVICE_SECONDS)
            try:
                _clear_boot_attempts()
            except Exception as exc:  # noqa: BLE001 - the next pass tries again.
                worker_print(f"start records were not cleared: {exc}")

    threading.Thread(target=clear_periodically, daemon=True, name="boot-attempts").start()


def _forget_refused_start() -> None:
    """This start lost the worker lock; the version it would have run is healthy."""
    from openprogram.operations.self_update.versions import layout, store

    runtime = layout.current_runtime()
    if runtime is not None:
        store.forget_last_attempt(runtime.layout, runtime.version)


def _remember_worker_environment() -> None:
    """What the controller gives a worker it starts where no service does."""
    from openprogram.operations.self_update.delivery.controller import save_worker_environment
    from openprogram.operations.self_update.versions import layout

    if layout.managed() and not trial_mode():
        try:
            save_worker_environment()
        except (OSError, ValueError) as exc:
            worker_print(f"worker environment not saved: {exc}")


def _register_install() -> None:
    """List this install among the version store's users (``versions/users.py``).

    An install from before the list existed is added on its first start, so
    the last uninstaller knows the store is no longer needed.
    """
    from openprogram.operations.self_update.versions import users

    if trial_mode():
        return
    try:
        users.register_running_install()
    except (OSError, ValueError) as exc:
        worker_print(f"install not listed among the version store's users: {exc}")


def trial_mode() -> bool:
    """Whether this worker is a candidate version's trial.

    A trial runs on a sanitized copy of the owner's data, on its own port, so
    nothing it does may reach the outside or repeat live work. The Web server,
    the runtime, the tools, the job runner and startup recovery still run,
    because their code is what the trial is for. These do not start: the
    scheduler and cron ticker, the always-on ticker, channels, MCP servers,
    the release and plugin update checks, service installation, the
    self-update recovery sweep, the idle-session watcher, the nightly
    reorganizer, the daily tool output collection, config hooks, the
    subscription catalog refresher and the Programs watcher.
    """
    return os.environ.get("OPENPROGRAM_TRIAL", "").strip() == "1"


def _clean_finished_updates() -> None:
    """Remove what finished updates left: sealed versions, or staged payloads."""
    from openprogram.operations.self_update.store import SelfUpdateStore
    from openprogram.operations.self_update.versions import retention
    from openprogram.operations.self_update.versions.layout import current_runtime
    from openprogram.state.paths import get_state_dir

    runtime = current_runtime()
    if runtime is not None:
        retention.run(runtime.layout, get_state_dir())
        return
    pruned = SelfUpdateStore().prune_payloads()
    if pruned:
        worker_print(f"self-update: removed the payloads of {len({p.parent for p in pruned})} finished updates")


def _answers_healthz(port: int) -> bool:
    import urllib.error
    import urllib.request

    # No proxy: this is our own loopback listener.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _settle_managed_version(port: int) -> None:
    """After a healthy start: adopt a version move and report direct edits."""
    from openprogram.operations.self_update.control import undo
    from openprogram.operations.self_update.delivery import owner_notice
    from openprogram.operations.self_update.versions import layout, unapplied

    runtime = layout.current_runtime()
    if runtime is None or trial_mode():
        return
    for _ in range(_HEALTHY_START_POLLS):
        if _answers_healthz(port):
            break
        time.sleep(1.0)
    else:
        return
    try:
        moved = undo.reconcile_manual_move(runtime.layout, runtime.version, tell=owner_notice.tell)
        if moved is not None:
            worker_print(f"version: adopted {moved.version} as the local line")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"version move could not be adopted: {exc}")
    try:
        files = unapplied.report_pending(runtime.layout, owner_notice.tell)
        if files:
            worker_print(f"version: {files} directly edited file(s) were set aside")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"direct edits could not be reported: {exc}")


#: The stop request file was written (``request_stop``).
_STOP_TOPIC = "worker.stop"
#: Where file events cannot be had, the request file is read this often, so
#: the shutdown fits in the five seconds ``stop_worker`` waits before it kills.
_STOP_POLL_SECONDS = 0.25
#: How long the main thread waits at a time. A signal interrupts a blocking
#: wait on POSIX, so there it only has to look in case an event was lost; on
#: Windows a lock wait does not return for Ctrl-C, so it wakes every second
#: to let the interrupt through.
_STOP_WAKE_SECONDS = 1.0 if sys.platform == "win32" else 300.0


def _wait_for_stop(wait=None) -> None:
    """Hold the main thread until the worker is asked to stop.

    A signal raises ``KeyboardInterrupt`` out of the wait. A stop request
    file does the same where no signal can be sent (``request_stop``): its
    file event wakes the wait at once (``host/_platform/changes``), and
    without file events it is read every :data:`_STOP_POLL_SECONDS`.
    ``wait(listener)`` replaces the wait in tests.
    """
    from openprogram.host._platform import changes

    from .lifecycle import stop_requested
    from .paths import stop_request_path

    request = stop_request_path()
    changes.watch(request.parent, _STOP_TOPIC, names={request.name})
    if wait is None:
        def wait(listener):
            return changes.wait_for(listener, resync=_STOP_WAKE_SECONDS, poll=_STOP_POLL_SECONDS)
    with changes.listen(_STOP_TOPIC) as listener:
        while True:
            if stop_requested():
                raise KeyboardInterrupt
            wait(listener)


def run_foreground() -> int:
    """Run the worker in the current process. Blocks until SIGTERM / Ctrl-C."""
    from openprogram.host.process_logging import configure_process_logging
    from openprogram.operations.worker.lifecycle import rotate_worker_log

    trial = trial_mode()
    rotated = rotate_worker_log()  # before this start's lines: the log stays bounded
    configure_process_logging("INFO")  # records join the worker log beside its status lines
    if rotated:
        worker_print("worker log rotated (previous contents in worker.log.1)")
    _raise_open_file_limit()
    lock = WorkerLock()
    if not lock.try_acquire():
        holder = lock.holder_pid
        worker_print(
            "another worker is already running"
            + (f" (PID {holder})" if holder is not None else "")
            + ". Exiting."
        )
        _forget_refused_start()
        return 1

    web_port = _settle_web_port()
    if web_port is None:
        # A held port is not this version's failure to start.
        _forget_refused_start()
        lock.release()
        return 1

    _remember_worker_environment()
    _register_install()
    # A request left for an earlier worker with this PID is not for this one.
    from .lifecycle import clear_stop_request
    clear_stop_request()

    # The user's Programs live in one directory; an older profile kept them
    # in workflows/ and applications/. Move them before anything loads.
    try:
        from openprogram.state.program_directory import migrate_user_directory
        migrate_user_directory(log=lambda message, *args: worker_print("" + message % args))
    except Exception as exc:  # noqa: BLE001
        worker_print(f"programs migration failed: {type(exc).__name__}: {exc}")

    try:
        from openprogram.operations.migrations.open_permissions import run as migrate_open_permissions
        if migrate_open_permissions():
            worker_print("open permissions: stored data updated")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"open permissions migration failed: {type(exc).__name__}: {exc}")

    try:
        from openprogram.operations.migrations.computer_use_rename import run as migrate_computer_use_names
        if migrate_computer_use_names():
            worker_print("computer_use: stored tool names and rules updated")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"computer_use rename failed: {type(exc).__name__}: {exc}")

    # This process now owns the profile: read the sessions that were still
    # running before anything can ask about them.
    try:
        from openprogram.operations.self_update.versions.store import migration_progress
        from openprogram.runtime.execution.store import default_store
        with migration_progress():
            _execution_store_ready = default_store()
        worker_print("execution store: ready")
    except Exception as exc:  # noqa: BLE001
        _execution_store_ready = None
        worker_print(f"execution store unavailable: {type(exc).__name__}: {exc}")

    from openprogram.models.providers.catalog.initialization import initialize_provider_runtime

    try:
        initialize_provider_runtime()
    except BaseException:
        lock.release()
        raise

    # Functions execute in isolated child processes. Those children may
    # durably enqueue agent jobs, but only this singleton worker owns the
    # profile lock required to claim and execute them. Keep the dispatcher
    # alive even when no WebSocket job action has initialized it yet.
    from openprogram.runtime.jobs import get_runner as get_job_runner
    from openprogram.runtime.jobs.runner import shutdown_runner as shutdown_job_runner

    # Jobs, reminders and always-on delivery reach the chat channels and
    # speech to text through the core links these packages register on
    # import; register them before the dispatcher can run anything.
    import openprogram.interfaces.channels  # noqa: F401
    import openprogram.interfaces.speech  # noqa: F401

    try:
        from openprogram.state.store.session.files.migration import run_startup_migration
        run_startup_migration()
        worker_print("session migration: ready")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"session migration deferred: {exc}")

    # File events from the operating system, for every loop that waits on
    # a change instead of looking every few seconds (host/_platform/changes).
    from openprogram.host._platform import changes as _changes
    _changes.start_watching()
    get_job_runner()
    from openprogram.operations.worker import subsystems as _subsystems
    _subsystems.record("job_runner", _subsystems.RUNNING)
    worker_print("job dispatcher: running")

    # Bring up the webui first — that's the worker's primary job. Single
    # port: this process serves the API, /ws AND the frontend export.
    from openprogram.interfaces.server import start_web
    port = web_port
    start_web(port=port, open_browser=False)
    write_port_file(port)
    write_pid_file()
    worker_print(f"webui WS at ws://127.0.0.1:{port}/ws")
    _clear_boot_attempts_while_serving()

    # Warm the provider cache in a background thread so the first HTTP
    # request (e.g. /api/providers/list when the user opens /programs)
    # doesn't have to do the 3-5s probe itself.
    def _warm_providers() -> None:
        try:
            from openprogram.interfaces.server import runtime_management as rm
            rm._init_providers()
        except Exception as exc:  # noqa: BLE001
            worker_print(f"provider warm-up failed: {exc}")

    threading.Thread(target=_warm_providers, daemon=True, name="provider-warmup").start()

    subscription_refresh_stop = None
    subscription_refresh_thread = None
    if not trial:
        try:
            from openprogram.models.providers.catalog.subscription_refresh import (
                start_subscription_catalog_refresher,
            )

            subscription_refresh_stop, subscription_refresh_thread = (
                start_subscription_catalog_refresher()
            )
            worker_print("subscription catalog refresh running")
        except Exception as exc:  # noqa: BLE001
            worker_print(f"subscription catalog refresh failed to start: {exc}")

    # Frontend build gate — the static export (apps/web/out/) is served by
    # this same process; just make sure it's fresh. Synchronous: the UI
    # isn't usable before it exists anyway. Failure is non-fatal (the
    # API/TUI still work; / returns 503 with a hint).
    if os.environ.get("OPENPROGRAM_NO_WEB", "").strip() not in ("1", "true", "yes"):
        try:
            from openprogram.interfaces.server.frontend import ensure_frontend_built
            worker_print("web: checking frontend export…")
            ensure_frontend_built()
        except Exception as exc:  # noqa: BLE001
            worker_print(f"web: frontend build failed: {exc}")

    if trial:
        worker_print("trial: background services and outside connections stay off")
    recovery_ready = False
    if not trial:
        try:
            from openprogram.operations.self_update.control.recovery import recover_pending_updates
            recovery_ready = recover_pending_updates()
        except Exception as exc:
            worker_print(f"self-update recovery failed: {type(exc).__name__}")
    if recovery_ready:
        try:
            _clean_finished_updates()
        except Exception as exc:  # noqa: BLE001
            worker_print(f"self-update payload cleanup failed: {type(exc).__name__}")
    if not trial:
        try:
            from openprogram.operations.self_update.control.watchdog import start as start_update_watchdog
            start_update_watchdog()
        except Exception as exc:  # noqa: BLE001
            worker_print(f"self-update watchdog failed to start: {type(exc).__name__}")

    if not trial:
        # Full tool outputs in the profile's shared folder that no
        # conversation names any more go once a day.
        try:
            from openprogram.state.store.session.files.tool_output_gc import start_daily
            start_daily()
        except Exception as exc:  # noqa: BLE001
            worker_print(f"tool output collection failed to start: {type(exc).__name__}")

    threading.Thread(
        target=_settle_managed_version, args=(port,), daemon=True, name="version-settle",
    ).start()

    channels = _ChannelSupervisor()
    from openprogram.operations.worker import subsystems
    if not trial:
        started_channels = channels.sync()
        channels.start_watcher()
        for channel_name in started_channels:
            subsystems.record(f"channel:{channel_name}", subsystems.RUNNING)
        if started_channels:
            worker_print(f"channels: {', '.join(started_channels)}")
        else:
            worker_print("channels: none configured (worker still running; "
                         "accounts linked later start on their own)")

    scheduler_stop = None
    scheduler_thread = None
    if not trial:
        try:
            from openprogram.programs.tools.jobs.cron.worker import start_in_worker

            if recovery_ready:
                scheduler_stop, scheduler_thread = start_in_worker()
                subsystems.record("scheduler", subsystems.RUNNING)
                worker_print("scheduler: running")
            else:
                subsystems.record("scheduler", subsystems.DEFERRED)
                worker_print("scheduler: deferred by self-update recovery failure")
        except Exception as exc:  # noqa: BLE001
            subsystems.record("scheduler", subsystems.FAILED, f"{type(exc).__name__}: {exc}")
            worker_print(f"scheduler failed to start: {exc}")

        # Always-on Agents — wake each enabled Agent when its next wake is due.
        try:
            from openprogram.runtime.conversation.always_on.ticker import start_in_worker as _start_always_on

            if recovery_ready:
                _start_always_on()
                subsystems.record("always_on", subsystems.RUNNING)
                worker_print("always-on Agents: running")
            else:
                subsystems.record("always_on", subsystems.DEFERRED)
                worker_print("always-on Agents: deferred by self-update recovery failure")
        except Exception as exc:  # noqa: BLE001
            subsystems.record("always_on", subsystems.FAILED, f"{type(exc).__name__}: {exc}")
            worker_print(f"always-on Agents failed to start: {exc}")

        # Memory subsystem — nightly reorganisation + session-end writer.
        try:
            from openprogram.state.memory.scheduler import start_nightly_reorganizer
            from openprogram.state.memory.session_watcher import start_idle_session_watcher
            start_nightly_reorganizer()
            start_idle_session_watcher()
            worker_print("memory: nightly reorganisation + session-end writer running")
        except Exception as exc:  # noqa: BLE001
            worker_print(f"memory subsystem failed to start: {exc}")

    # 事件层 B 类桥 — auth 的 subscribe/_emit 信号翻译进统一总线
    # (docs/design/proactive/event-layer.md §3)。
    try:
        from openprogram.runtime.events.bridges import install_event_bridges
        if install_event_bridges():
            worker_print("event bridges installed (auth → bus)")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"event bridges failed to install: {exc}")

    # 用户 shell 订阅者 — config.json 顶层 "hooks" 注册到总线（gate 型
    # 事件挂同步闸门，notify 型挂后台命令）。改配置重启生效。
    if not trial:
        try:
            from openprogram.runtime.events import install_config_hooks
            _n_hooks = install_config_hooks()
            if _n_hooks:
                worker_print(f"{_n_hooks} config hook subscriber(s) registered")
        except Exception as exc:  # noqa: BLE001
            worker_print(f"config hooks failed to install: {exc}")

    # Channel question bridge — push runtime.ask/form questions from a
    # channel session into that chat so the user can /answer them.
    try:
        from openprogram.interfaces.channels.inbound._question_bridge import install_question_bridge
        install_question_bridge()
        worker_print("channel question bridge installed")
    except Exception as exc:  # noqa: BLE001
        worker_print(f"channel question bridge failed to install: {exc}")

    # Programs watcher — auto-detect agentic harnesses installed at
    # runtime (clone into agentics/ or `programs install`) so their
    # functions go live + the UI refreshes without a restart.
    if not trial:
        try:
            from openprogram.programs._host.watcher import start_in_worker as _start_programs_watch
            if _start_programs_watch() is not None:
                worker_print("programs watcher running (auto-detect installs)")
        except Exception as exc:  # noqa: BLE001
            worker_print(f"programs watcher failed to start: {exc}")

    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt

    # SIGBREAK is Ctrl-Break in a Windows console; it exists only there.
    for _name in ("SIGTERM", "SIGBREAK"):
        _number = getattr(_signal, _name, None)
        if _number is None:
            continue
        try:
            _signal.signal(_number, _on_sigterm)
        except (ValueError, OSError):
            pass

    try:
        # Block until asked to stop — webui server runs on its own
        # threads/loop, so we just need to keep the main thread alive. If
        # channels are running, we also want to react when they all die
        # (worker can keep running with just webui though, so don't exit).
        _wait_for_stop()
    except KeyboardInterrupt:
        print()
        worker_print("stopping...")
        from openprogram.runtime.execution.recovery.restart import prepare_shutdown
        try:
            prepare_shutdown(get_job_runner())
        except Exception:
            import logging
            logging.getLogger(__name__).exception("could not prepare restart checkpoints")
        channels.shutdown()
        if scheduler_stop is not None:
            scheduler_stop.set()
        if subscription_refresh_stop is not None:
            subscription_refresh_stop.set()
        # Shared join budget, not 3s per thread: `openprogram stop` waits
        # 5s after SIGTERM before force-killing, and two channel bots at
        # 3s each already blew that window — every stop ended in SIGKILL.
        # Threads that don't stop in time drop on process exit anyway.
        _join_deadline = time.time() + 2.0
        for label, t in channels.threads_holding_work():
            t.join(timeout=max(0.1, _join_deadline - time.time()))
            if t.is_alive():
                print(f"[{label}] still running; drops on process exit")
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=max(0.1, _join_deadline - time.time()))
        if subscription_refresh_thread is not None:
            subscription_refresh_thread.join(
                timeout=max(0.1, _join_deadline - time.time())
            )
    finally:
        from openprogram.runtime.conversation.run_control import begin_worker_shutdown
        begin_worker_shutdown()
        from openprogram.interfaces.server.server import stop_server
        stop_server()
        shutdown_job_runner()
        _clear_boot_attempts()
        lock.release()
        clear_pid_file()
        clear_port_file()
        clear_stop_request()
    return 0
