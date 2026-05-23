"""macOS launchd integration for the persistent worker.

Writes ``~/Library/LaunchAgents/ai.openprogram.worker.plist`` and
loads it with ``launchctl``. The plist runs ``<python> -m openprogram
worker run`` under the current user's session, KeepAlive=true so it
restarts if it crashes, RunAtLoad=true so it starts at login.

Logs land in ``<state-dir>/worker.log`` (same file as the detached
``worker start`` flow) so users see one history regardless of how the
worker was launched.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from openprogram.worker import paths as worker_paths

LABEL = "ai.openprogram.worker"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _build_plist() -> dict[str, Any]:
    log = str(worker_paths.log_path())
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-u", "-m", "openprogram", "worker", "run"],
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(Path.home()),
        },
        "WorkingDirectory": str(Path.home()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
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


def install() -> int:
    plist_file = _plist_path()
    plist_file.parent.mkdir(parents=True, exist_ok=True)

    # Stop any in-flight worker before swapping the plist; otherwise the
    # newly-loaded job fights the existing PID for the lock file.
    from openprogram.worker.lifecycle import current_worker_pid, stop_worker
    if current_worker_pid() is not None:
        stop_worker()

    # If a previous plist is loaded, unload first so launchctl picks up
    # the new contents instead of caching the old definition.
    if plist_file.exists():
        _launchctl("unload", str(plist_file))

    with open(plist_file, "wb") as f:
        plistlib.dump(_build_plist(), f)

    rc, msg = _launchctl("load", "-w", str(plist_file))
    if rc != 0:
        print(f"launchctl load failed (rc={rc}): {msg}")
        return rc

    print(f"openprogram worker installed as launchd service ({LABEL}).")
    print(f"  plist: {plist_file}")
    print(f"  logs:  {worker_paths.log_path()}")
    print()
    print("It is now running and will start automatically at login.")
    print("Check status:  openprogram worker status")
    return 0


def uninstall() -> int:
    plist_file = _plist_path()
    if not plist_file.exists():
        print(f"openprogram worker: no launchd service installed at {plist_file}.")
        return 0
    rc, msg = _launchctl("unload", "-w", str(plist_file))
    if rc != 0:
        print(f"launchctl unload failed (rc={rc}): {msg}")
        # Continue and remove the plist file anyway — the user wants it gone.
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
    rc, msg = _launchctl("list", LABEL)
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
