"""Remember, once a day, which release is the latest.

The worker checks in the background and writes the answer to one small file, so
``/api/system/version`` can say whether an update exists without touching the
network. Nothing is downloaded or installed here.
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
from pathlib import Path
import threading
import time

from . import github
from .detect import InstallMethod, detect_install_method
from openprogram.host._platform import filesystem

CHECK_INTERVAL_SECONDS = 24 * 3600
RETRY_INTERVAL_SECONDS = 6 * 3600

_log = logging.getLogger(__name__)
_started = False
_start_lock = threading.Lock()


def _record_path() -> Path:
    from openprogram.state.paths import get_state_dir

    return Path(get_state_dir()) / "latest-release.json"


def _read() -> dict:
    try:
        record = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _parts(version: object) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(part) for part in str(version).split("."))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None


def view(current_version: str) -> dict:
    """The fields ``/api/system/version`` adds. All empty until a check has passed."""
    record = _read()
    latest, current = record.get("latest_version"), _parts(current_version)
    newest = _parts(latest)
    return {
        "latestVersion": latest if newest else None,
        "updateAvailable": bool(newest and current and newest > current),
        "latestCheckedAt": record.get("checked_at") if newest else None,
    }


def refresh() -> bool:
    """Ask for the latest release and record it. A failed check keeps the last answer."""
    release = github.latest_release()
    if release is None:
        return False
    record = {
        "latest_version": str(release["tag_name"])[1:],
        "checked_at": time.time(),
        "found_through": github.release_source(release)["via"],
    }
    path = _record_path()
    staging = path.with_name(f"{path.name}.staging")
    staging.write_text(json.dumps(record) + "\n", encoding="utf-8")
    filesystem.replace_file(staging, path)
    return True


def _seconds_until_due() -> float:
    last = _read().get("checked_at")
    if not isinstance(last, (int, float)):
        return 0.0
    return max(0.0, last + CHECK_INTERVAL_SECONDS - time.time())


def _run() -> None:
    while True:
        wait = _seconds_until_due()
        if wait > 0:
            time.sleep(wait)
            continue
        try:
            passed = refresh()
        except Exception:
            _log.debug("the daily release check failed", exc_info=True)
            passed = False
        if not passed:
            time.sleep(RETRY_INTERVAL_SECONDS)


def start() -> None:
    """Start the daily check once, on installs that take published releases."""
    global _started
    if detect_install_method() is not InstallMethod.MANAGED_RELEASE:
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, name="openprogram-release-check", daemon=True).start()


def current_version() -> str:
    try:
        return importlib.metadata.version("openprogram")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


__all__ = ["CHECK_INTERVAL_SECONDS", "current_version", "refresh", "start", "view"]
