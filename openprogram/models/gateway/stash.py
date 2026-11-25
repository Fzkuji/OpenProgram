"""Original config values, kept until they are put back.

``<state>/gateway/harness-stash.json`` holds, per harness, the file written,
each key's value before OpenProgram first wrote it (or that it was absent),
and what OpenProgram wrote. The originals are saved before the first write
to the harness's file and an entry is deleted only after the write that
restores it succeeded. A key whose value is no longer what OpenProgram wrote
was changed by the user since: it is reported as drift and never restored
over.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from typing import Any

from .keys import gateway_dir

_lock = threading.RLock()


def _path():
    return gateway_dir() / "harness-stash.json"


def load() -> dict[str, Any]:
    with _lock:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as exc:
            # Never treat a damaged stash as "nothing to restore".
            raise RuntimeError(f"the saved original harness settings cannot be read: {exc}") from exc
        return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    from openprogram.host._platform.filesystem import replace_file
    replace_file(tmp, path)


def entry(harness: str) -> dict[str, Any] | None:
    return load().get(harness)


def remember(harness: str, path: str, originals: dict[str, tuple[bool, Any]]) -> None:
    """Keep each key's original value unless one is already kept."""
    with _lock:
        data = load()
        record = data.setdefault(harness, {"path": path, "originals": {}, "wrote": {}})
        record["path"] = path
        for key, (had, value) in originals.items():
            record["originals"].setdefault(key, {"had": had, "value": value} if had else {"had": False})
        _save(data)


def wrote(harness: str, values: dict[str, dict[str, Any]]) -> None:
    """Record what was written: ``{key: {"set": value}}`` or ``{key: {"deleted": True}}``."""
    with _lock:
        data = load()
        record = data.get(harness)
        if record is None:
            return
        record["wrote"] = values
        _save(data)


def forget(harness: str) -> None:
    with _lock:
        data = load()
        if data.pop(harness, None) is not None:
            _save(data)
