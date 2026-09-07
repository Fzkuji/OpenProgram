"""Session-attributed resource uses reported by integrations, not host discovery."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

from openprogram.processes.store import process_identity


def display_target(value: str) -> str:
    """Resource identity only: do not persist URL credentials or bearer parameters."""
    value = str(value or "")
    if "://" in value:
        try:
            url = urlsplit(value)
            host = url.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            if url.port:
                host += f":{url.port}"
            value = urlunsplit((url.scheme, host, url.path, "", ""))
        except ValueError:
            value = ""
    return value[:1024]


class ResourceUseStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from openprogram.paths import get_state_dir
            path = get_state_dir() / "session-resources.db"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS resource_uses (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, execution_id TEXT,
                kind TEXT NOT NULL, title TEXT NOT NULL, target TEXT NOT NULL,
                started_at REAL NOT NULL, owner_pid INTEGER NOT NULL,
                owner_identity TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS resource_session ON resource_uses(session_id)")
        if os.name == "posix":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, session_id, execution_id, kind, title, target):
        identity = process_identity(os.getpid())
        if not identity:
            raise RuntimeError("resource owner identity unavailable")
        record_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO resource_uses VALUES (?,?,?,?,?,?,?,?,?)", (
                record_id, session_id, execution_id, str(kind)[:64], str(title)[:160],
                display_target(target), time.time(), os.getpid(), identity,
            ))
        return record_id

    def remove(self, record_id):
        with self.connect() as db:
            db.execute("DELETE FROM resource_uses WHERE id=?", (record_id,))

    def list(self, session_id, execution_ids=()):
        ids = tuple(execution_ids)
        suffix = " OR execution_id IN (" + ",".join("?" for _ in ids) + ")" if ids else ""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM resource_uses WHERE session_id=?" + suffix +
                              " ORDER BY started_at DESC", (session_id, *ids)).fetchall()
        owners = {}
        result = []
        for row in rows:
            pid = row["owner_pid"]
            if pid not in owners:
                owners[pid] = process_identity(pid)
            if owners[pid] != row["owner_identity"]:
                self.remove(row["id"])
                continue
            result.append({key: row[key] for key in (
                "id", "session_id", "execution_id", "kind", "title", "target", "started_at",
            )})
        return result


@contextmanager
def resource_use(kind: str, title: str, target: str = "", *, store=None):
    """Report use of a complete software/environment object with trusted attribution.

    Identify the actual application, VM, container or remote environment.
    Commands, scripts, processes, files and execution output belong in Activity.
    Leaving the context releases the use record, never the external resource.
    Integrations retain their existing control and teardown responsibilities.
    """
    from openprogram.agent.run_control import get_current_session_id
    if not get_current_session_id():
        yield
        return
    record_id = None
    try:
        from openprogram.processes import current_owner
        session_id, execution_id, _ = current_owner()
        store = store or ResourceUseStore()
        record_id = store.add(session_id, execution_id, kind, title, target)
    except Exception as exc:
        # Display tracking must not change the backend's execution contract.
        logging.getLogger(__name__).warning("Resource tracking unavailable: %s", type(exc).__name__)
    try:
        yield
    finally:
        if record_id:
            try:
                store.remove(record_id)
            except Exception as exc:
                logging.getLogger(__name__).warning("Resource release unavailable: %s", type(exc).__name__)
