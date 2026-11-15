"""Background watcher — auto-detect Programs installed or changed at runtime.

Polls the user's Programs directory (``~/.openprogram/programs/``) and
the owner-recorded sources for changes
(a package edited and checked, an application directory dropped in, a
harness cloned by ``openprogram programs install``) and, when a fingerprint
shifts, re-runs discovery via ``registry.rescan`` and broadcasts
``programs:changed`` so connected UIs refresh their function list — no
worker restart needed.

**Why polling, not OS file events:** the whole project's rule is
"works on every OS, installed in one step, no surprise native deps".
``watchdog`` (inotify / FSEvents / ReadDirectoryChangesW) is a
third-party dep whose backends behave differently per platform — exactly
the cross-platform fragility we avoid. The Programs directory has a handful of
top-level entries, so an ``os.scandir`` fingerprint every couple seconds
is noise-level cost and behaves identically everywhere. Mirrors the
thread model of ``memory/session_watcher.py``.

Manual ``POST /api/programs/refresh`` and this watcher call the SAME
core (``rescan``) and emit the SAME event — two triggers, one path.

Scope: started only in the **web worker** (that's where the live
function list + WS clients are). CLI / one-shot runs load agentics once
at import and don't need it. For harnesses it detects **additions** only —
see ``rescan``'s docstring on why removals need a restart. Installed
applications are watched to the file: a changed source is disposed and
loaded again with a new digest.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 2.0  # seconds


def _fingerprint(directory: str) -> tuple:
    """A cheap signature of the top level of ``directory``: the set of
    entry names plus each one's mtime. Changes when a harness dir is
    added / removed / its mtime bumps. Sorted → order-stable."""
    try:
        entries = []
        with os.scandir(directory) as it:
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
    from openprogram.runtime.events import emit_ws_frame
    emit_ws_frame({"type": "programs:changed", "added": added})


def _catalog_fingerprint(base: str) -> tuple:
    from openprogram.programs._host.sources import owner_controlled_program_sources
    from openprogram.state.paths import program_sources_path
    roots = {base}
    roots.update(os.path.dirname(row["path"]) for row in owner_controlled_program_sources())
    try:
        stat = os.stat(program_sources_path())
        source_stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        source_stamp = None
    return (source_stamp, tuple((root, _fingerprint(root)) for root in sorted(roots)),
            _package_fingerprint(), _application_fingerprint())


def _application_fingerprint() -> tuple:
    """The catalog and the source trees of the installed applications.

    Only a process that loads installed applications (the worker) watches
    them; a change makes ``rescan`` dispose and reload the application.
    """
    from openprogram.programs._host.ui import loader
    return loader.watch_stamp() if loader.active() else ()


def _package_fingerprint() -> tuple:
    """The files and Git state of every user package."""
    try:
        from openprogram.programs._host.packages import root_fingerprint
        return root_fingerprint()
    except Exception:
        return ()


def start_in_worker(
    *, poll_interval: float = DEFAULT_POLL_INTERVAL
) -> "threading.Thread | None":
    """Spawn the daemon watcher thread. Returns it, or None if disabled.

    Disable with env ``OPENPROGRAM_NO_PROGRAMS_WATCH=1``.
    """
    if os.environ.get("OPENPROGRAM_NO_PROGRAMS_WATCH", "").strip() in (
        "1", "true", "yes"
    ):
        logger.info("programs watcher disabled by env")
        return None

    from openprogram.state.program_directory import programs_root
    from openprogram.programs._host.registry import rescan
    programs = str(programs_root())

    def _loop() -> None:
        prev = _catalog_fingerprint(programs)
        while True:
            time.sleep(poll_interval)
            try:
                cur = _catalog_fingerprint(programs)
                if cur == prev:
                    continue
                prev = cur
                result = rescan()
                added = result.get("added") or []
                logger.info("programs watcher: catalog changed, added %s", added)
                _emit_changed(added)
            except Exception as e:  # noqa: BLE001
                logger.warning("programs watcher pass failed: %s", e)

    t = threading.Thread(target=_loop, name="programs-watcher", daemon=True)
    t.start()
    return t
