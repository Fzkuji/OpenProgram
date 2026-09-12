"""Event log — one JSON line per completed typed dispatch on the singleton.

Routing: ``~/.openprogram/sessions/<sid>/events.jsonl`` when the event
carries a session whose directory already exists (session directories are
created by the session store, never by the logger — a stray session id in
metadata must not mint a phantom session directory); the shared
``~/.openprogram/logs/events.jsonl`` otherwise. Files rotate to ``.1``
(replacing the previous ``.1``) past 5 MB. Gate verdicts land on the same
line as a ``gate`` field; an observer-phase gate emit is not written first.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import nullcontext
from pathlib import Path

_LOG_MAX_BYTES = 5 * 1024 * 1024
_log_write_lock = threading.Lock()


def _event_log_path(ev) -> Path:
    from openprogram.paths import get_state_dir
    base = Path(get_state_dir())
    sid = ev.metadata.get("session") if isinstance(ev.metadata, dict) else None
    if sid:
        sid = str(sid)
        sessions_root = base / "sessions"
        try:
            from openprogram.store.session.placement import is_deleted
            if is_deleted(sessions_root, sid):
                return base / "logs" / "events.jsonl"
        except Exception:
            pass
        try:
            from openprogram.store.session.session_store import default_store
            store = default_store()
            # A test or an embedding process can change the active home/profile
            # after the process-wide store was constructed.  Do not consult a
            # store rooted in another profile for the current event.
            if Path(store.root_path) == base / "sessions":
                sess_dir = store._session_dir(sid)
                if sess_dir.is_dir():
                    return sess_dir / "events.jsonl"
        except Exception:
            pass
        # Resolve the durable location record even when the process singleton
        # was created under another profile. This covers nested project
        # sessions without constructing another SessionStore.
        try:
            locations_path = sessions_root / "locations.json"
            locations = json.loads(locations_path.read_text(encoding="utf-8"))
            recorded = locations.get(sid) if isinstance(locations, dict) else None
            if recorded and Path(recorded).is_dir():
                return Path(recorded) / "events.jsonl"
        except (OSError, json.JSONDecodeError):
            pass
        sess_dir = sessions_root / sid
        if sess_dir.is_dir():
            return sess_dir / "events.jsonl"
        try:
            from openprogram.store.project import project_store
            project = project_store.project_for_session(sid)
            if project is not None and not getattr(project, "is_default", False):
                nested = sessions_root / "projects" / project.id / sid
                if nested.is_dir():
                    return nested / "events.jsonl"
        except Exception:
            pass
    return base / "logs" / "events.jsonl"


def log_event(ev, gate: dict | None = None) -> None:
    """Append one JSON line; rotate past 5 MB. Never raises — logging must
    not break the emitting path."""
    try:
        record = {
            "id": ev.id, "ts": ev.ts, "type": ev.type, "origin": ev.origin,
            "payload": ev.payload, "metadata": ev.metadata,
        }
        if gate is not None:
            record["gate"] = gate
        line = json.dumps(record, ensure_ascii=False, default=str)
        sid = ev.metadata.get("session") if isinstance(ev.metadata, dict) else None
        lock = nullcontext()
        if sid:
            from openprogram.store.session.session_lock import session_interprocess_lock
            lock = session_interprocess_lock(str(sid), reentrant=True)
        # Keep the session lock across path validation and mkdir. This makes
        # deletion publish its tombstone before the logger can create files.
        with lock:
            path = _event_log_path(ev)
            with _log_write_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if path.exists() and path.stat().st_size > _LOG_MAX_BYTES:
                        os.replace(path, str(path) + ".1")
                except OSError:
                    pass
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
    except Exception:
        pass
