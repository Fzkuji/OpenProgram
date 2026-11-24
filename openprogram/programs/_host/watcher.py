"""Background watcher — auto-detect Programs installed or changed at runtime.

Watches the user's Programs directory (``~/.openprogram/programs/``), the
owner-recorded sources and the installed applications' source trees for
changes (a package edited and checked, an application directory dropped
in, a harness cloned by ``openprogram programs install``) and, when a
fingerprint shifts, re-runs discovery via ``registry.rescan`` and
broadcasts ``programs:changed`` so connected UIs refresh their function
list — no worker restart needed.

**File events, then a fingerprint.** The operating system tells the
Worker that something under a watched directory changed
(``host/_platform/changes.watch``, topic :data:`TOPIC`); only then is
the fingerprint taken again, and only a different fingerprint rescans,
so an editor's temporary file or a Git fetch costs one fingerprint and
no rescan. The set of watched directories follows the sources: after
each pass it is brought in line with the catalog and the recorded
sources. Where file events cannot be had, the watcher looks every
``poll_interval`` seconds as it used to; everywhere it looks again
every :data:`RESYNC_SECONDS` in case an event was lost. A package
replace put off for running calls is looked at again
:data:`RETRY_SECONDS` later.

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

from openprogram.host._platform import changes

logger = logging.getLogger(__name__)

#: File events under the watched directories wake the watcher on this topic.
TOPIC = "programs.sources"
#: Only where file events cannot be had: a pass stats every source file of every package.
DEFAULT_POLL_INTERVAL = 5.0
#: A pass even without an event, in case one was lost.
RESYNC_SECONDS = 300.0
#: A failed pass, or a package replace put off for running calls, is tried again this soon.
RETRY_SECONDS = 5.0


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

    A change makes ``rescan`` dispose and reload the application. The stamp
    is of the files alone: it was once empty until the worker had loaded its
    applications, so the first pass after every start saw a change, scanned
    everything again and told every open window to fetch its Programs.
    """
    from openprogram.programs._host.ui import loader
    return loader.watch_stamp()


def _package_fingerprint() -> tuple:
    """The files and Git state of every user package."""
    try:
        from openprogram.programs._host.packages import root_fingerprint
        return root_fingerprint()
    except Exception:
        return ()


def _retries() -> int:
    from openprogram.programs._host import packages
    return packages._retries


def _watch_targets(programs: str) -> set[tuple[str, bool, frozenset[str] | None]]:
    """The directories whose changes can move the fingerprint:
    ``(path, recursive, names)`` for ``changes.watch``."""
    from openprogram.programs._host.sources import owner_controlled_program_sources
    from openprogram.programs._host.ui import loader
    from openprogram.state.paths import program_sources_path

    # Every package's files and Git state, and the directories dropped in.
    targets: set[tuple[str, bool, frozenset[str] | None]] = {(programs, True, None)}
    sources = program_sources_path()
    targets.add((str(sources.parent), False, frozenset({sources.name})))
    for row in owner_controlled_program_sources():
        targets.add((os.path.dirname(row["path"]), False, None))
    catalog = loader.catalog.catalog_store.path()
    targets.add((str(catalog.parent), False, frozenset({catalog.name})))
    for root in loader.watch_roots():
        targets.add((str(root), True, None))
    return targets


class _Watcher:
    """One pass per wake: fingerprint, rescan on a difference, follow the sources."""

    def __init__(self, programs: str) -> None:
        self.programs = programs
        self.previous: tuple | None = None
        self.watched: set[tuple[str, bool, frozenset[str] | None]] = set()
        #: When to look again without an event; ``None`` when nothing asks for it.
        self.retry_at: float | None = None

    def begin(self) -> None:
        try:
            self.previous = _catalog_fingerprint(self.programs)
        except Exception as e:  # noqa: BLE001
            logger.warning("programs watcher pass failed: %s", e)
            self.retry_at = time.time() + RETRY_SECONDS
        self.follow_sources()

    def step(self) -> None:
        self.retry_at = None
        try:
            current = _catalog_fingerprint(self.programs)
            if current != self.previous:
                self.previous = current
                retries = _retries()
                from openprogram.programs._host.registry import rescan
                result = rescan()
                added = result.get("added") or []
                logger.info("programs watcher: catalog changed, added %s", added)
                _emit_changed(added)
                if _retries() != retries:
                    # A replace waits for running calls; its retry moves the fingerprint.
                    self.retry_at = time.time() + RETRY_SECONDS
        except Exception as e:  # noqa: BLE001
            logger.warning("programs watcher pass failed: %s", e)
            self.retry_at = time.time() + RETRY_SECONDS
        self.follow_sources()

    def follow_sources(self) -> None:
        """Watch the directories the catalog and the recorded sources name now."""
        try:
            wanted = _watch_targets(self.programs)
        except Exception:  # noqa: BLE001 - the directories watched so far stay
            logger.debug("programs watcher: sources not read", exc_info=True)
            return
        gone = {path for path, _recursive, _names in self.watched - wanted}
        for path in gone:
            changes.unwatch(path, TOPIC)
        for path, recursive, names in sorted(wanted, key=lambda t: (t[0], t[1], sorted(t[2] or ()))):
            if path in gone or (path, recursive, names) not in self.watched:
                changes.watch(path, TOPIC, recursive=recursive, names=names)
        self.watched = wanted


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
    watcher = _Watcher(str(programs_root()))
    listener = changes.listen(TOPIC)

    def _loop() -> None:
        watcher.begin()
        while True:
            changes.wait_for(listener, resync=RESYNC_SECONDS, poll=poll_interval,
                             due=watcher.retry_at)
            watcher.step()

    t = threading.Thread(target=_loop, name="programs-watcher", daemon=True)
    t.start()
    return t
