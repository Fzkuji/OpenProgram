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
from pathlib import Path

_LOG_MAX_BYTES = 5 * 1024 * 1024
_log_write_lock = threading.Lock()


def _event_log_path(ev) -> Path:
    base = Path.home() / ".openprogram"
    sid = ev.metadata.get("session") if isinstance(ev.metadata, dict) else None
    if sid:
        sess_dir = base / "sessions" / str(sid)
        if sess_dir.is_dir():
            return sess_dir / "events.jsonl"
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
