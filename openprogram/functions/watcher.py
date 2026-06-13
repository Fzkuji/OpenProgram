"""Background watcher — auto-detect agentic programs installed at runtime.

Polls ``functions/agentics/`` for changes (a harness cloned in, or
``openprogram programs install``) and, when the directory's fingerprint
shifts, re-runs discovery via ``_registry.rescan`` and broadcasts
``programs:changed`` so connected UIs refresh their function list — no
worker restart needed.

**Why polling, not OS file events:** the whole project's rule is
"works on every OS, installed in one step, no surprise native deps".
``watchdog`` (inotify / FSEvents / ReadDirectoryChangesW) is a
third-party dep whose backends behave differently per platform — exactly
the cross-platform fragility we avoid. ``agentics/`` has a handful of
top-level entries, so an ``os.scandir`` fingerprint every couple seconds
is noise-level cost and behaves identically everywhere. Mirrors the
thread model of ``memory/session_watcher.py``.

Manual ``POST /api/programs/refresh`` and this watcher call the SAME
core (``rescan``) and emit the SAME event — two triggers, one path.

Scope: started only in the **web worker** (that's where the live
function list + WS clients are). CLI / one-shot runs load agentics once
at import and don't need it. Detects **additions** only — see
``rescan``'s docstring on why removals need a restart.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 2.0  # seconds


def _fingerprint(agentics_dir: str) -> tuple:
    """A cheap signature of the top level of ``agentics_dir``: the set of
    entry names plus each one's mtime. Changes when a harness dir is
    added / removed / its mtime bumps. Sorted → order-stable."""
    try:
        entries = []
        with os.scandir(agentics_dir) as it:
            for e in it:
                if e.name.startswith((".", "__")):
                    continue
                try:
                    entries.append((e.name, int(e.stat().st_mtime)))
                except OSError:
                    entries.append((e.name, 0))
        return tuple(sorted(entries))
    except OSError:
        return ()


def _emit_changed(added: list[str]) -> None:
    """Broadcast ``programs:changed`` to connected UIs.

    步 4：走总线（ws.frame 事件），不再 import webui；帧内容不变。
    """
    from openprogram.agent.event_bus import emit_ws_frame
    emit_ws_frame({"type": "programs:changed", "added": added})


def start_in_worker(
    *, poll_interval: float = DEFAULT_POLL_INTERVAL
) -> "threading.Thread | None":
    """Spawn the daemon watcher thread. Returns it, or None if disabled
    / the agentics dir can't be located.

    Disable with env ``OPENPROGRAM_NO_PROGRAMS_WATCH=1``.
    """
    if os.environ.get("OPENPROGRAM_NO_PROGRAMS_WATCH", "").strip() in (
        "1", "true", "yes"
    ):
        logger.info("programs watcher disabled by env")
        return None

    from openprogram.functions._registry import _default_agentics_dir, rescan
    agentics_dir = _default_agentics_dir()
    if not agentics_dir or not os.path.isdir(agentics_dir):
        logger.debug("programs watcher: no agentics dir, not starting")
        return None

    def _loop() -> None:
        prev = _fingerprint(agentics_dir)
        while True:
            time.sleep(poll_interval)
            try:
                cur = _fingerprint(agentics_dir)
                if cur == prev:
                    continue
                prev = cur
                result = rescan(agentics_dir)
                added = result.get("added") or []
                if added:
                    logger.info("programs watcher: detected %s", added)
                    _emit_changed(added)
            except Exception as e:  # noqa: BLE001
                logger.debug("programs watcher pass failed: %s", e)

    t = threading.Thread(target=_loop, name="programs-watcher", daemon=True)
    t.start()
    return t
