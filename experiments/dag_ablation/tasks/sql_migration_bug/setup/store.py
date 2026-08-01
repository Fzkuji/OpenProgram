import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def put(self, key, value):
        # BUG: plain INSERT explodes on an existing key
        self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                          (key, value))
        self.conn.execute("INSERT INTO history (key, value) VALUES (?, ?)",
                          (key, value))
        # BUG: never committed

    def get(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def recent(self, key, n=3):
        """Most recent n values for key, newest first."""
        rows = self.conn.execute(
            "SELECT value FROM history WHERE key = ? "
            "ORDER BY id DESC LIMIT ?", (key, n - 1)).fetchall()
        # BUG: LIMIT n - 1 returns one too few
        return [r[0] for r in rows]
