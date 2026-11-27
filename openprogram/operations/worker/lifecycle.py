"""Lifecycle controls for the persistent worker process.

The worker hosts the webui WebSocket server even when no channel is
configured. Channel polling remains an optional add-on.

Public functions:

    spawn_detached()      — fork a background worker, return immediately
    stop_worker()         — ask the live worker to stop (escalates to a kill)
    restart_worker()      — stop + spawn_detached
    print_status()        — human-readable status report
    claim_worker_port()   — the port a starting worker listens on

Which process is the live worker, its port and its interpreter are read in
``openprogram.host.worker.process``, where the layers below operations
read them too.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Optional

from openprogram.host._platform.process_ownership import no_window_creation_flags

from openprogram.host._platform.filesystem import replace_file
from openprogram.host.worker import paths
from openprogram.host.worker.process import (
    _process_alive,
    current_worker_pid,
    explicit_web_port,
    read_worker_port,
    recorded_web_port,
    resolve_worker_port,
    worker_executable,
)

_log = logging.getLogger(__name__)


# port file


def write_port_file(port: int) -> None:
    paths.port_path().write_text(f"{port}\n", encoding="utf-8")


def clear_port_file() -> None:
    try:
        paths.port_path().unlink(missing_ok=True)
    except OSError:
        pass


def _record_web_port(port: int, *, replace: bool) -> int:
    """Write the profile's port; the first writer wins unless ``replace``.

    The App reads the same file (apps/desktop/main/worker/web-port.js).
    """
    target = paths.web_port_record_path()
    if replace:
        staged = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        staged.write_text(f"{port}\n", encoding="utf-8")
        replace_file(staged, target)
        return port
    try:
        with open(target, "x", encoding="utf-8") as stream:
            stream.write(f"{port}\n")
        return port
    except FileExistsError:
        return recorded_web_port() or port


def claim_worker_port(*, held: int | None = None) -> tuple[int, str | None]:
    """The port a starting worker listens on, and the setting that named it.

    The second value is ``OPENPROGRAM_WEB_PORT`` or ``ui.web_port`` when the
    user set the port, and None when the profile chose it. A profile's first
    start takes the lowest free port from 18100 up and records it, so a
    second account on the same computer, or a program already on 18100,
    never leaves a new user without a port. ``held`` is the port the caller
    found taken: a chosen port moves to the next free one and the record
    follows; a port the user set never moves.
    """
    explicit = explicit_web_port()
    if explicit is not None:
        return explicit
    from openprogram.host.worker.ports import first_free_web_port

    recorded = recorded_web_port()
    if held is None and recorded is not None:
        return recorded, None
    exclude = frozenset() if held is None else frozenset({held})
    return _record_web_port(first_free_web_port(exclude), replace=held is not None), None


def find_running_webui() -> tuple[Optional[int], Optional[int], str]:
    """This profile's running worker as ``(port, pid, source)``.

    ``(port, pid, "managed")`` while the worker holds its lock and has named
    its port; ``(None, None, "none")`` otherwise. A listener without this
    profile's lock is never reported as ours.
    """
    pid = current_worker_pid()
    if pid is not None:
        port = read_worker_port()
        if port is not None:
            return port, pid, "managed"
    return None, None, "none"


# pid file


def write_pid_file() -> None:
    """Write current PID + start timestamp. Called by the running worker."""
    paths.pid_path().write_text(f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")


def clear_pid_file() -> None:
    try:
        paths.pid_path().unlink(missing_ok=True)
    except OSError:
        pass


# stop request


def request_stop(pid: int) -> None:
    """Ask the worker to shut down in order: checkpoints, channels, server.

    POSIX sends SIGTERM. Windows has no signal one process can send another:
    ``os.kill`` there is ``TerminateProcess``, which ends the worker with
    nothing run, its restart checkpoints unwritten and its web server child
    left holding the port. The request is a file naming the PID instead; the
    worker's main loop reads it (:func:`stop_requested`).
    """
    if sys.platform != "win32":
        os.kill(pid, signal.SIGTERM)
        return
    if not _process_alive(pid):
        raise ProcessLookupError(pid)
    target = paths.stop_request_path()
    staged = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    staged.write_text(f"{pid}\n", encoding="utf-8")
    replace_file(staged, target)


def stop_requested() -> bool:
    """Whether a stop was asked of this process. Called by the running worker."""
    try:
        raw = paths.stop_request_path().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return raw == str(os.getpid())


def clear_stop_request() -> None:
    try:
        paths.stop_request_path().unlink(missing_ok=True)
    except OSError:
        pass


# start / stop


def use_named_runtime_for_cli() -> None:
    """Keep direct managed CLI execution under the same application identity."""
    executable = worker_executable()
    if Path(executable).resolve() == Path(sys.executable).resolve():
        return
    # Keep interpreter options only, never treat CLI tool arguments as Python flags.
    flags = []
    original = iter(getattr(sys, "orig_argv", [sys.executable])[1:])
    for argument in original:
        if argument in {"--", "-"} or not argument.startswith("-"):
            break
        boundary = None
        takes_value = False
        if not argument.startswith("--"):
            for index, option in enumerate(argument[1:], 1):
                if option in "mc":
                    boundary = index
                    break
                if option in "WX":
                    takes_value = index == len(argument) - 1
                    break
        if boundary is not None:
            if boundary > 1:
                flags.append(argument[:boundary])
            break
        flags.append(argument)
        if takes_value or argument == "--check-hash-based-pycs":
            flags.append(next(original))
    if sys.flags.isolated and "-I" not in flags:
        flags.append("-I")
    if sys.flags.dont_write_bytecode and "-B" not in flags:
        flags.append("-B")
    os.execv(executable, [executable, *flags, "-m", "openprogram", *sys.argv[1:]])


def _detached_worker_command(flags=None, *, windows: bool | None = None) -> list[str]:
    """Preserve isolation, bytecode policy and Windows' UTF-8 mode across the worker re-exec."""
    from openprogram.host._platform.runtime_layout import current_runtime

    runtime = current_runtime()
    if runtime is not None:
        # The launcher adds -I -B itself and applies the version guard.
        return [str(runtime.layout.launcher()), "-u", "-m", "openprogram", "worker", "run"]
    active_flags = sys.flags if flags is None else flags
    command = [worker_executable()]
    if active_flags.isolated:
        command.append("-I")
    if active_flags.dont_write_bytecode:
        command.append("-B")
    # The Windows launchers start Python with -X utf8, and -I drops PYTHONUTF8.
    if (os.name == "nt" if windows is None else windows) and getattr(active_flags, "utf8_mode", 0):
        command.extend(["-X", "utf8"])
    command.extend(["-u", "-m", "openprogram", "worker", "run"])
    return command


WORKER_LOG_LIMIT_BYTES = 16 * 1024 * 1024
WORKER_LOG_GENERATIONS = 3


def rotate_worker_log(path: Path | None = None, *, limit_bytes: int = WORKER_LOG_LIMIT_BYTES,
                      keep: int = WORKER_LOG_GENERATIONS) -> bool:
    """Shift a large worker log into numbered generations; True when it did.

    launchd and ``spawn_detached`` hold the log open in append mode for the
    worker's whole life, so the file cannot be renamed away under them:
    its contents move to ``worker.log.1`` (older generations shift up, the
    oldest past ``keep`` goes) and the file itself is truncated, which an
    O_APPEND writer follows without noticing. Runs once at worker start.
    """
    log_file = paths.log_path() if path is None else Path(path)
    try:
        if log_file.stat().st_size < limit_bytes:
            return False
    except OSError:
        return False
    try:
        for index in range(keep, 0, -1):
            source = log_file.with_name(f"{log_file.name}.{index}")
            if not source.exists():
                continue
            if index == keep:
                source.unlink()
            else:
                replace_file(source, log_file.with_name(f"{log_file.name}.{index + 1}"))
        with open(log_file, "rb") as current, open(log_file.with_name(f"{log_file.name}.1"), "wb") as previous:
            while chunk := current.read(1024 * 1024):
                previous.write(chunk)
        os.truncate(log_file, 0)
    except OSError:
        logging.getLogger(__name__).debug("worker log rotation failed", exc_info=True)
        return False
    return True


def spawn_detached(
    *,
    prefer_service: bool = True,
    on_spawn: Callable[[int], None] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Start the worker through its installed service or as a detached child.

    ``on_spawn`` runs immediately after ``Popen`` returns, before readiness is
    polled.  Release installers use it to durably record the detached session
    leader so cleanup can terminate the whole process group even before the
    worker has written its ordinary lock and PID files.  ``environment`` applies
    to a detached child only; a service has its own.
    """
    if prefer_service:
        from openprogram.operations.worker import services

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
            env=environment,
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
            from openprogram.host._platform.processes import kill_process_tree

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


def _starting_worker_pid(timeout: float = 30.0) -> Optional[int]:
    """The PID of a worker that holds the lock but has not named itself yet.

    A worker takes its lock first and writes its PID file a few seconds
    later, once its startup work is done (much later on a slow Windows
    start). A stop in that window must wait for the PID rather than report
    "not running" and leave the worker up.
    """
    from openprogram.host.worker.lock import WorkerLock

    probe = WorkerLock()
    if probe.try_acquire():
        probe.release()
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.2)
        pid = current_worker_pid()
        if pid is not None:
            return pid
        probe = WorkerLock()
        if probe.try_acquire():
            probe.release()
            return None
    return None


def stop_worker(*, prefer_service: bool = True) -> int:
    """Stop the service-owned or detached worker, escalating when required."""
    if prefer_service:
        from openprogram.operations.worker import services

        service_result = services.stop_if_installed()
        if service_result is not None:
            return service_result

    pid = current_worker_pid()
    if pid is None:
        pid = _starting_worker_pid()
    if pid is None:
        print("openprogram: not running.")
        return 0
    print(f"Stopping openprogram (PID {pid})...")
    try:
        request_stop(pid)
    except ProcessLookupError:
        print("Process already gone.")
        clear_pid_file()
        clear_port_file()
        return 0
    except PermissionError:
        print(f"Can't signal PID {pid} — owned by another user.")
        return 1
    try:
        return _await_stop(pid)
    finally:
        clear_stop_request()


def _await_stop(pid: int) -> int:
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _process_alive(pid):
            print("Stopped.")
            return 0
        time.sleep(0.2)

    print(f"PID {pid} didn't exit when asked; force-killing.")
    # kill_process_tree handles both POSIX SIGKILL and Windows taskkill;
    # also takes out any uvicorn / channel-bot children the worker
    # spawned. signal.SIGKILL doesn't exist on Windows Python so we
    # can't do ``os.kill(pid, signal.SIGKILL)`` directly there.
    from openprogram.host._platform.processes import kill_process_tree
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
        from openprogram.operations.worker import services

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


def print_status(*, channel_status: Callable[[], list[dict]]) -> int:
    """One-screen status report for the worker.

    ``channel_status`` lists every channel account with its state; the CLI
    passes ``openprogram.interfaces.channels.list_status``.
    """
    port, pid, source = find_running_webui()

    if source == "none":
        print("openprogram: not running")
        from openprogram.host.worker.ports import port_in_use, port_owner_hint

        own_port = resolve_worker_port()
        if port_in_use(own_port):
            print(f"  Port {own_port} is in use by another program or account.")
            hint = port_owner_hint(own_port)
            if hint:
                print(hint)
        print()
        print("  Start it with:  openprogram")
        print("  Or install as a service (auto-start at login):  openprogram worker install")
        return 0


    started = _worker_start_time(pid) if pid is not None else None
    age = ""
    if started is not None:
        age = f", up {_format_duration(time.time() - started)}"

    port_str = f", port {port}" if port else ""
    print(f"openprogram: running (PID {pid}{port_str}{age})")
    print(f"  logs: {paths.log_path()}")

    try:
        rows = channel_status()
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
        # Status line only.
        _log.debug("channel status unavailable", exc_info=True)
    return 0
