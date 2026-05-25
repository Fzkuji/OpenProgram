"""Plugin auto-update — periodic background check for upgradable plugins.

Mirrors claude-code's ``pluginAutoupdate.ts``. Spawns one background
thread on server startup. Every ``_INTERVAL_SECS`` it walks installed
plugins and asks each source manager whether a newer version is
available. Results land in a module-level dict the API can read.

We DO NOT auto-install upgrades. The user gets a notification (WS
broadcast) and can decide to run ``openprogram plugins update`` or
click the badge in the UI.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable


# How often to re-check. Default 6h matches claude-code's default and
# keeps egress to PyPI / npm reasonable.
_INTERVAL_SECS = 6 * 3600

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
# {plugin_name: {current, latest, source}} — only entries where
# current < latest are kept.
_available: dict[str, dict[str, str]] = {}
_on_change_callbacks: list[Callable[[dict], None]] = []


def get_available_updates() -> dict[str, dict[str, str]]:
    with _lock:
        return {k: dict(v) for k, v in _available.items()}


def register_callback(cb: Callable[[dict], None]) -> None:
    """Subscribe to update notifications. ``cb(payload)`` runs when
    new updates are discovered; payload mirrors ``get_available_updates()``."""
    with _lock:
        if cb not in _on_change_callbacks:
            _on_change_callbacks.append(cb)


def _fire_change() -> None:
    snapshot = get_available_updates()
    with _lock:
        cbs = list(_on_change_callbacks)
    for cb in cbs:
        try:
            cb(snapshot)
        except Exception:
            pass


def _check_pip(dist_name: str, current_version: str) -> str | None:
    """Return ``latest_version`` if newer than ``current_version``, else None.
    Talks to PyPI's JSON API — works without authentication."""
    import urllib.request
    import urllib.error
    try:
        url = f"https://pypi.org/pypi/{dist_name}/json"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    latest = (data.get("info") or {}).get("version") or ""
    if not latest or latest == current_version:
        return None
    if _is_newer(latest, current_version):
        return latest
    return None


def _check_npm(pkg_name: str, current_version: str) -> str | None:
    """Same idea for npm registry."""
    import urllib.request
    import urllib.error
    try:
        url = f"https://registry.npmjs.org/{pkg_name}/latest"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    latest = data.get("version") or ""
    if not latest or latest == current_version:
        return None
    if _is_newer(latest, current_version):
        return latest
    return None


def _is_newer(latest: str, current: str) -> bool:
    """Best-effort semver compare. Falls back to lexical when versions
    aren't pure numeric. Anything we can't parse is treated as not-newer
    so we don't spam false positives."""
    def parts(v: str) -> list[int] | None:
        try:
            return [int(x) for x in v.split(".")[:3]]
        except (ValueError, IndexError):
            return None
    a, b = parts(latest), parts(current)
    if a and b:
        return a > b
    return False


def _scan() -> None:
    """Walk installed plugins, fill ``_available``. Catches errors
    per-plugin so one bad source doesn't block the whole scan."""
    from openprogram.plugins.loader import list_plugins
    new: dict[str, dict[str, str]] = {}
    for p in list_plugins():
        if not p.enabled or not p.loaded:
            continue
        src = getattr(p, "source", "") or ""
        current = p.manifest.version or "0.0.0"
        latest: str | None = None
        try:
            if src == "pip":
                latest = _check_pip(p.name, current)
            elif src == "npm":
                latest = _check_npm(p.name, current)
            # git / path sources have no reliable upstream-version
            # concept — skip.
        except Exception:
            latest = None
        if latest:
            new[p.name] = {
                "current": current,
                "latest": latest,
                "source": src,
            }
    with _lock:
        changed = new != _available
        _available.clear()
        _available.update(new)
    if changed:
        _fire_change()


def _run() -> None:
    # Wait a bit on first boot so we don't egress before the server
    # is even reachable.
    _stop.wait(60.0)
    while not _stop.is_set():
        try:
            _scan()
        except Exception:
            pass
        if _stop.wait(_INTERVAL_SECS):
            return


def start() -> None:
    """Start the watcher thread once. No-op on subsequent calls."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_run, name="plugin-autoupdate", daemon=True,
        )
        _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    t = _thread
    _thread = None
    if t is not None:
        t.join(timeout=2.0)


def force_check_now() -> dict[str, dict[str, str]]:
    """Synchronous scan — used by the API endpoint to refresh on demand."""
    _scan()
    return get_available_updates()
