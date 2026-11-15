"""``min_reader_schema``: how old a reader a SQLite store still accepts.

``PRAGMA user_version`` says which schema wrote a store. On its own it would
lock every older version out as soon as a migration adds a column, and a
retained version has to be able to open the store after a rollback. So a store
also keeps the oldest schema that can still read it. An additive migration
leaves that value alone; only a change an older reader cannot follow raises it.
"""
from __future__ import annotations

import sqlite3

KEY = "min_reader_schema"


def stored_floor(connection: sqlite3.Connection) -> int | None:
    """The store's recorded floor, or ``None`` when it records none."""
    try:
        row = connection.execute("SELECT value FROM store_meta WHERE key = ?", (KEY,)).fetchone()
    except sqlite3.OperationalError:
        return None
    try:
        return None if row is None else int(row[0])
    except (TypeError, ValueError):
        return None


def readable_when_newer(connection: sqlite3.Connection, known_schema: int) -> bool:
    """Whether code that knows ``known_schema`` may use a newer store as it is."""
    floor = stored_floor(connection)
    return floor is not None and floor <= known_schema


def record_floor(connection: sqlite3.Connection, floor: int) -> None:
    """Raise the recorded floor to ``floor``. It never goes down."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    current = stored_floor(connection)
    if current is not None and current >= floor:
        return
    connection.execute(
        "INSERT INTO store_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (KEY, str(floor)),
    )
