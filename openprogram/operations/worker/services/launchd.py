"""macOS launchd integration for the persistent worker.

Writes ``~/Library/LaunchAgents/ai.openprogram.worker.plist`` (or a
profile-specific job for a named profile) and loads it with ``launchctl``. The plist runs ``<python> -m openprogram
worker run`` under the current user's session, KeepAlive=true so it
restarts if it crashes, RunAtLoad=true so it starts at login.

Logs land in ``<state-dir>/worker.log`` (same file as the detached
``worker start`` flow) so users see one history regardless of how the
worker was launched.
"""
from __future__ import annotations

import hashlib
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openprogram.state.paths import get_active_profile
from openprogram.operations.worker import paths as worker_paths

LABEL = "ai.openprogram.worker"


def _label() -> str:
    """Return the launchd label for the active profile.

    The default profile keeps the historical label. A named profile gets its
    own job, so installing or restarting it never rewrites or restarts the
    default profile's worker.
    """

    profile = get_active_profile()
    if profile is None:
        return LABEL
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:16]
    return f"{LABEL}.profile-{digest}"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_label()}.plist"


def _build_plist() -> dict[str, Any]:
    from openprogram.operations.worker.lifecycle import _detached_worker_command
    from openprogram.operations.worker.services.systemd import _service_environment

    log = str(worker_paths.log_path())
    environment = _service_environment()
    environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment["HOME"] = str(Path.home())
    return {
        "Label": _label(),
        "ProgramArguments": _detached_worker_command(),
        "EnvironmentVariables": environment,
        "WorkingDirectory": str(Path.home()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _launchctl(*args: str) -> tuple[int, str]:
    if shutil.which("launchctl") is None:
        return 127, "launchctl not found"
    try:
        out = subprocess.run(
            ["launchctl", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as e:
        return 1, str(e)
    return out.returncode, (out.stdout + out.stderr).strip()


def _loaded_state() -> tuple[bool | None, int, str]:
    rc, msg = _launchctl("list", _label())
    if rc == 0:
        return True, rc, msg
    if rc == 113 or "could not find service" in msg.lower():
        return False, rc, msg
    return None, rc, msg


def install() -> int:
    plist_file = _plist_path()
    plist_file.parent.mkdir(parents=True, exist_ok=True)

    # Disable KeepAlive before stopping the worker. Stopping first lets the
    # old job immediately spawn another process before launchctl is unloaded.
    if plist_file.exists():
        loaded, rc, msg = _loaded_state()
        if loaded is None:
            print(f"launchctl status failed (rc={rc}): {msg}")
            return rc or 1
        if loaded:
            rc, msg = _launchctl("unload", str(plist_file))
            if rc != 0:
                print(f"launchctl unload failed (rc={rc}): {msg}")
                return rc

    from openprogram.operations.worker.lifecycle import current_worker_pid, stop_worker
    if current_worker_pid() is not None and stop_worker(prefer_service=False) != 0:
        return 1

    with open(plist_file, "wb") as f:
        plistlib.dump(_build_plist(), f)

    rc, msg = _launchctl("load", "-w", str(plist_file))
    if rc != 0:
        print(f"launchctl load failed (rc={rc}): {msg}")
        return rc

    print(f"openprogram worker installed as launchd service ({_label()}).")
    print(f"  plist: {plist_file}")
    print(f"  logs:  {worker_paths.log_path()}")
    print()
    print("It is now running and will start automatically at login.")
    print("Check status:  openprogram worker status")
    return 0


def start_if_installed() -> int | None:
    """Start the managed service, or return ``None`` when none is installed.

    Reinstalling rewrites ``ProgramArguments`` for the current runtime before
    loading, so a start after an upgrade never relaunches an old interpreter.
    """

    if not _plist_path().is_file():
        return None
    return install()


def stop_if_installed() -> int | None:
    """Stop the launchd-owned worker without KeepAlive starting another.

    Signalling the worker alone is not a stop: KeepAlive relaunches it at
    once, and that new process would import whatever is on disk mid-upgrade.
    Unloading the job stops it and keeps it stopped until the next load or
    login.
    """

    plist_file = _plist_path()
    if not plist_file.is_file():
        return None
    loaded, rc, msg = _loaded_state()
    if loaded is None:
        print(f"launchctl status failed (rc={rc}): {msg}")
        return rc or 1
    if loaded:
        rc, msg = _launchctl("unload", str(plist_file))
        if rc != 0:
            print(f"launchctl unload failed (rc={rc}): {msg}")
            return rc

    # Wait out a worker that is still exiting, and stop a detached one an
    # older CLI started beside the service. Bypass service dispatch to avoid
    # recursing back into this function.
    from openprogram.operations.worker.lifecycle import current_worker_pid, stop_worker

    if current_worker_pid() is not None:
        return stop_worker(prefer_service=False)
    print(f"openprogram worker stopped via launchd ({_label()}).")
    return 0


def restart_if_installed() -> int | None:
    """Refresh and restart an installed service using the current runtime."""

    if not _plist_path().is_file():
        return None
    return install()


def uninstall() -> int:
    plist_file = _plist_path()
    if not plist_file.exists():
        print(f"openprogram worker: no launchd service installed at {plist_file}.")
        return 0
    loaded, rc, msg = _loaded_state()
    if loaded is None:
        print(f"launchctl status failed (rc={rc}): {msg}")
        return rc or 1
    if loaded:
        rc, msg = _launchctl("unload", "-w", str(plist_file))
        if rc != 0:
            print(f"launchctl unload failed (rc={rc}): {msg}")
            return rc
    try:
        plist_file.unlink()
    except OSError as e:
        print(f"failed to remove {plist_file}: {e}")
        return 1
    print(f"openprogram worker uninstalled (removed {plist_file}).")
    return 0


def status() -> int:
    plist_file = _plist_path()
    print(f"launchd plist: {plist_file}")
    print(f"  installed: {'yes' if plist_file.exists() else 'no'}")
    if not plist_file.exists():
        return 0
    rc, msg = _launchctl("list", _label())
    if rc == 0:
        print("  loaded:    yes")
        # launchctl list <label> prints a property-list-like dict; skim it
        # for PID / LastExitStatus.
        for line in msg.splitlines():
            line = line.strip().rstrip(";")
            if line.startswith('"PID"') or line.startswith('"LastExitStatus"'):
                print(f"  {line}")
    else:
        print("  loaded:    no")
    return 0
