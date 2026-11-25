"""Third-party harnesses: Claude Code and Codex sessions on this computer, read only.

Reads the session files those agents write, counts each call once,
prices it at API list prices and keeps an index in
``<state>/harnesses.db``, apart from OpenProgram's own usage ledger.
Nothing is written to the agents' files or configuration.

  refresh()          scan what changed since the last scan (≤ one at a time)
  start_warm_scan()  first scan in a background thread at Worker start
  report(...)        totals, models, clients, days, sessions, quota

See docs/reference/design/integrations/third-party-harnesses.html.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from types import ModuleType
from typing import Optional

from . import claude_code, codex
from .files import read_new, unchanged
from .store import RETAIN_S, Batch, Store

_log = logging.getLogger(__name__)

READERS: tuple[ModuleType, ...] = (claude_code, codex)
FRESH_S = 5.0

_scan_lock = threading.Lock()
_last_scan = 0.0
_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        from openprogram.state.paths import get_state_dir
        _store = Store(get_state_dir() / "harnesses.db")
    return _store


def scan() -> None:
    """Read every file that changed since the last scan."""
    global _last_scan
    with _scan_lock:
        began = time.time()
        st = store()
        with st.connection() as conn:
            for reader in READERS:
                try:
                    _scan_reader(st, conn, reader, began)
                except Exception:
                    _log.warning("reading %s sessions failed", reader.LABEL, exc_info=True)
            st.prune(conn, began)
            _last_scan = time.time()
            st.set_meta(conn, "scanned_at", f"{_last_scan:.3f}")
        took = _last_scan - began
        if took > 1:
            _log.info("third-party harness scan took %.1f s", took)


def _scan_reader(st: Store, conn, reader: ModuleType, now: float) -> None:
    marks = st.marks(conn, reader.AGENT)
    oldest = now - RETAIN_S
    for home in reader.homes():
        # Oldest file first: a call copied into a resumed or forked session
        # keeps the session it was first made in.
        for path, main in sorted(reader.files(home), key=lambda f: (_born(f[0]), f[0])):
            known = marks.get(path)
            prev, state = known if known else (None, {})
            if prev is None:
                try:
                    if os.stat(path).st_mtime < oldest:
                        continue
                except OSError:
                    continue
            elif unchanged(path, prev):
                continue
            read = read_new(path, prev)
            if read is None:
                continue
            if read.restarted:
                state = {}
            batch = Batch(agent=reader.AGENT)
            reader.parse(path, read.lines, state, batch, main)
            st.apply(conn, batch, path, read.mark, state)
        names = getattr(reader, "titles", None)
        if names is not None:
            _apply_titles(st, conn, reader, home)


def _born(path: str) -> float:
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    return getattr(st, "st_birthtime", None) or st.st_mtime


def _apply_titles(st: Store, conn, reader: ModuleType, home) -> None:
    index = home / "session_index.jsonl"
    try:
        stamp = f"{index.stat().st_size}:{index.stat().st_mtime_ns}"
    except OSError:
        return
    key = f"titles:{index}"
    if st.meta(conn, key) == stamp:
        return
    st.set_names(conn, reader.AGENT, reader.titles(home))
    st.set_meta(conn, key, stamp)


def refresh(max_age: float = FRESH_S) -> bool:
    """Scan when the last scan is older than ``max_age``. Returns at once,
    without scanning, while another scan runs (the first one at start)."""
    if time.time() - _last_scan < max_age:
        return False
    if not _scan_lock.acquire(blocking=False):
        return False
    _scan_lock.release()
    scan()
    return True


def start_warm_scan() -> None:
    """The first scan, in the background, so the Third-Party Harnesses page finds the index ready."""
    if not enabled():
        return
    threading.Thread(target=_warm, name="openprogram-harness-scan", daemon=True).start()


def _warm() -> None:
    try:
        scan()
    except Exception:
        _log.warning("third-party harness scan failed", exc_info=True)


def enabled() -> bool:
    from openprogram.state.settings import setting_value
    return bool(setting_value("usage.third_party_harnesses"))


def reader(agent: str) -> Optional[ModuleType]:
    return next((r for r in READERS if r.AGENT == agent), None)


from .report import report  # noqa: E402

__all__ = ["scan", "refresh", "start_warm_scan", "report", "enabled", "store", "READERS", "reader"]
