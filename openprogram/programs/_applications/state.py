"""Persistent application bindings and operation metadata; executions stay canonical."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import sqlite3

from .catalog import home


@contextmanager
def connect():
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "state.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS instances (
          id TEXT PRIMARY KEY, app_id TEXT NOT NULL, owner TEXT NOT NULL,
          project_id TEXT NOT NULL, project_path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (
          id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, request_key TEXT NOT NULL,
          fingerprint TEXT NOT NULL, definition TEXT NOT NULL, operation TEXT NOT NULL,
          input TEXT NOT NULL, result TEXT, error TEXT, question TEXT,
          UNIQUE(instance_id, request_key));
        CREATE TABLE IF NOT EXISTS events (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          payload TEXT NOT NULL);
        """)
        with db:
            yield db
    finally:
        db.close()


def instance(app: dict, project_id: str = "") -> dict:
    from openprogram.agent.authority import owner_principal_id
    owner = owner_principal_id()
    project_path = ""
    if app["scope"] == "project":
        from openprogram.store.project import get_project
        project = get_project(project_id) if project_id else None
        if project is None:
            raise ValueError("select a project for this application")
        project_path = project.path
    else:
        project_id = ""
    key = hashlib.sha256(json.dumps([owner, app["id"], project_id]).encode()).hexdigest()
    with connect() as db:
        db.execute("INSERT INTO instances VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET project_path=excluded.project_path",
                   (key, app["id"], owner, project_id, project_path))
        return dict(db.execute("SELECT * FROM instances WHERE id=?", (key,)).fetchone())


def get_instance(key: str) -> dict:
    from openprogram.agent.authority import owner_principal_id
    with connect() as db:
        row = db.execute("SELECT * FROM instances WHERE id=? AND owner=?", (key, owner_principal_id())).fetchone()
    if row is None:
        raise FileNotFoundError("application instance not found")
    return dict(row)


def emit(run_id: str, payload: dict):
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError("application event exceeds 1 MiB")
    with connect() as db:
        db.execute("INSERT INTO events(run_id,payload) VALUES(?,?)", (run_id, encoded))


def data(instance_id: str, value=None, *, expected_version=None) -> dict:
    # Instance keys are generated hashes, never user-provided directory names.
    if len(instance_id) != 64 or any(c not in "0123456789abcdef" for c in instance_id):
        raise ValueError("invalid instance id")
    directory = home() / "data" / instance_id
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "data.sqlite", timeout=30)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, version INTEGER, value TEXT)")
        db.execute("INSERT OR IGNORE INTO state VALUES(1,0,'{}')")
        if expected_version is not None:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 4 * 1024 * 1024:
                raise ValueError("application state exceeds 4 MiB")
            changed = db.execute("UPDATE state SET value=?, version=version+1 WHERE id=1 AND version=?", (encoded, expected_version)).rowcount
            if not changed:
                raise ValueError("application state changed; reload before saving")
        version, raw = db.execute("SELECT version,value FROM state WHERE id=1").fetchone()
        return {"version": version, "value": json.loads(raw)}


def asset_token(instance_id: str, digest: str) -> str:
    """A read-only capability for one instance's immutable UI resources."""
    import secrets
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS asset_tokens(instance_id TEXT, digest TEXT, token TEXT, PRIMARY KEY(instance_id,digest))")
        db.execute("INSERT OR IGNORE INTO asset_tokens VALUES(?,?,?)", (instance_id, digest, secrets.token_hex(32)))
        return db.execute("SELECT token FROM asset_tokens WHERE instance_id=? AND digest=?", (instance_id, digest)).fetchone()[0]


def asset_definition(instance_id: str, digest: str, token: str) -> dict:
    import hmac
    from .catalog import get
    instance = get_instance(instance_id)
    definition = get(instance["app_id"])
    if digest != definition["digest"] or not hmac.compare_digest(asset_token(instance_id, digest), token):
        raise FileNotFoundError("application resource capability is invalid or revoked")
    return definition
