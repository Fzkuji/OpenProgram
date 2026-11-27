"""Reading another app's SQLite database without touching it.

After yetone/magpie ``internal/sessions/cursor.go`` ``OpenUntouched`` and
``internal/agent/aionui.go`` ``dbStatus`` (MIT License, Copyright (c)
yetone). An app's database is read, never written, whether the app runs or
not: with no write-ahead log it is opened read-only and immutable in place;
with one (the app running in WAL mode), the database and its log are copied
aside and the copy read, so no lock or checkpoint of OpenProgram's reaches
the app's files. :class:`DbStatus` keeps one read while neither file changed,
for at most :data:`READ_FOR` seconds, so a row polled by the settings page
does not copy a running app's database each time.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from ..edit.text import open_untouched

#: How long a read is reused while the files keep their size and time
#: (magpie ``reachedFor``: a coarse clock can miss a write that kept the size).
READ_FOR = 15.0


def _stamp(path: str) -> tuple[int, int]:
    try:
        st = os.stat(path)
    except OSError:
        return (-1, 0)
    return (st.st_size, st.st_mtime_ns)


class DbStatus:
    """The last answer of ``has(path, gateway) -> (has, known)`` for a database,
    reused while it and its log are unchanged (magpie ``dbStatus``)."""

    def __init__(self, has: Callable[[Path, str], tuple[bool, bool]]):
        self._has = has
        self._lock = threading.Lock()
        self._key: tuple = ()
        self._at = 0.0
        self._answer = (False, False)

    def read(self, path: Path, gateway: str) -> tuple[bool, bool]:
        key = (str(path), gateway, _stamp(str(path)), _stamp(str(path) + "-wal"))
        with self._lock:
            if self._answer[1] and key == self._key and time.monotonic() - self._at < READ_FOR:
                return self._answer
            # a read that failed is tried again on the next poll
            self._answer = self._has(Path(path), gateway)
            self._key, self._at = key, time.monotonic()
            return self._answer


def count(path: Path, sql: str, *args: object) -> tuple[bool, bool]:
    """``(rows > 0, known)`` for a ``SELECT count(*)`` on the database at
    ``path``: no database is ``(False, True)``, one that can't be read is
    unknown, never "not there"."""
    path = Path(path)
    try:
        path.stat()
    except FileNotFoundError:
        return False, True
    except OSError:
        return False, False
    try:
        with open_untouched(path) as conn:
            row = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return False, False
    return bool(row and row[0]), True
