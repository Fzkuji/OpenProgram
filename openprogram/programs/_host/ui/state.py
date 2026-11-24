"""Persistent application bindings and operation metadata; executions stay canonical."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import re
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
        CREATE TABLE IF NOT EXISTS conversations (
          instance_id TEXT NOT NULL, key TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE,
          PRIMARY KEY(instance_id, key));
        """)
        with db:
            yield db
    finally:
        db.close()


def instance(app: dict, project_id: str = "") -> dict:
    from openprogram.runtime.permissions.authority import owner_principal_id
    owner = owner_principal_id()
    project_path = ""
    if app["scope"] == "project":
        from openprogram.state.store.project import get_project
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
    from openprogram.runtime.permissions.authority import owner_principal_id
    with connect() as db:
        row = db.execute("SELECT * FROM instances WHERE id=? AND owner=?", (key, owner_principal_id())).fetchone()
    if row is None:
        raise FileNotFoundError("application instance not found")
    return dict(row)



def current_project_path(project_id: str) -> str:
    from pathlib import Path
    from openprogram.state.store.project import get_project
    from openprogram.state.store.project.location import bound_execution_state, refresh_project_location
    refresh_project_location(project_id)
    project = get_project(project_id)
    if project is None:
        raise ValueError("bound project no longer exists")
    from openprogram.state.store.project.project_store import folder_is_here
    if not folder_is_here(project):
        raise ValueError("the project's folder is on another machine; applications run on this computer")
    reason = bound_execution_state(project)
    if reason or not Path(project.path).is_dir():
        raise ValueError(f"project location is unavailable ({reason or 'missing'}); locate the project before running")
    return project.path


def refresh_instance_location(key: str) -> dict:
    value = get_instance(key)
    if value["project_id"]:
        value["project_path"] = current_project_path(value["project_id"])
        with connect() as db:
            db.execute("UPDATE instances SET project_path=? WHERE id=?", (value["project_path"], key))
    return value


#: What a window may call one of its conversations: a task's name, say.
CONVERSATION_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


def conversation(instance_id: str, key: str, folder: str) -> dict:
    """The conversation an application window keeps under ``key``.

    A window that declares ``chat`` shows the owner's composer for a
    conversation of its own: one per key (Auto Research keeps one per project
    and task). The first ask mints the conversation's id, a draft key the
    chat server adopts as the conversation's id on its first message, and
    the folder becomes an OpenProgram project the conversation works in.
    A window reaches only the conversations it was given here.
    """
    from pathlib import Path
    from .catalog import get
    instance = get_instance(instance_id)
    if "chat" not in get(instance["app_id"])["capabilities"]:
        raise ValueError("application does not declare chat")
    if not isinstance(key, str) or not CONVERSATION_KEY.match(key):
        raise ValueError("invalid conversation key")
    path = Path(str(folder or instance["project_path"] or "")).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("the conversation's folder is not a folder on this computer")
    from openprogram.state.store.project import project_store
    project = project_store.resolve_project(str(path))
    import secrets
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO conversations VALUES(?,?,?)",
                   (instance_id, key, "local_" + secrets.token_hex(16)))
        session_id = db.execute("SELECT session_id FROM conversations WHERE instance_id=? AND key=?",
                                (instance_id, key)).fetchone()[0]
    return {"session_id": session_id, "project_id": project.id, "folder": project.path}


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
    """A read-only capability for one instance's UI resources at one content digest."""
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
